"""Running one declared command with bounded output.

Every command the preflight runs is bounded in both output and wall clock, so a
noisy or hung prerequisite is a named finding rather than an unreadable log or
a job that never finishes.
"""

from __future__ import annotations

import dataclasses as dc
import os
import signal
import subprocess
import threading
import time
import typing as typ
from pathlib import Path

from .constants import LOG_BOUND_BYTES
from .diagnostics import TRUNCATION_PREFIX, timeout_message
from .models import Ran
from .resolution import resolve

if typ.TYPE_CHECKING:
    from .ports import Invocation

#: How much of one stream is read at a time.
_READ_CHUNK_BYTES: typ.Final[int] = 8192

#: How long the readers share to reach end-of-file after the command is gone.
#: A descendant that inherited the pipe can outlive the command, so the readers
#: are bounded rather than joined forever, and the bound is one period for the
#: run rather than one per stream.
_DRAIN_GRACE_SECONDS: typ.Final[int] = 5

#: How long a reader may take to leave once it has been told to stop. A polled
#: reader notices within one poll, so this bounds the wait for it.
_DRAIN_STOP_SECONDS: typ.Final[float] = 1.0

#: How long a polled reader waits before checking whether it should stop. The
#: reader is cancellable, so this is the worst-case delay in cleanup, not a
#: delay in the command itself.
_DRAIN_POLL_SECONDS: typ.Final[float] = 0.05


@dc.dataclass
class _Capture:
    """One stream's retained prefix, and how many bytes were dropped.

    The reader thread and the reporting thread both touch this, so both the
    append and the snapshot take the lock; a reader still blocked when the
    snapshot is taken must not be mutating the buffer underneath it.
    """

    kept: bytearray = dc.field(default_factory=bytearray)
    dropped: int = 0
    lock: threading.Lock = dc.field(default_factory=threading.Lock)

    def take(self, chunk: bytes) -> None:
        """Keep what still fits inside the bound, and count the remainder."""
        with self.lock:
            room = max(0, LOG_BOUND_BYTES - len(self.kept))
            kept = chunk[:room]
            self.kept.extend(kept)
            self.dropped += len(chunk) - len(kept)

    def snapshot(self) -> tuple[bytes, int]:
        """Return the retained bytes and the dropped count as one pair."""
        with self.lock:
            return bytes(self.kept), self.dropped


def _blocking_drain(stream: typ.IO[bytes], capture: _Capture) -> None:
    """Read *stream* to its end, keeping only the captured prefix."""
    try:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            capture.take(chunk)
    except (OSError, ValueError):
        # The stream was closed under the reader, or a descendant still holds
        # the write end. Either way the retained prefix is what the command
        # produced before cleanup.
        return


def _polled_drain(
    stream: typ.IO[bytes], capture: _Capture, stop: threading.Event
) -> None:
    """Read *stream*'s descriptor, noticing *stop* between polls.

    A read on a pipe whose write end a descendant still holds does not return,
    and closing the descriptor from another thread does not reliably wake it.
    Polling a non-blocking descriptor lets the reader leave of its own accord,
    so cleanup never waits on a writer that has outlived its command.
    """
    fd = stream.fileno()
    try:
        os.set_blocking(fd, False)
    except OSError:
        _blocking_drain(stream, capture)
        return
    while not stop.is_set():
        try:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
        except BlockingIOError:
            stop.wait(_DRAIN_POLL_SECONDS)
            continue
        except OSError:
            return
        if not chunk:
            return
        capture.take(chunk)


def _drain(stream: typ.IO[bytes], capture: _Capture, stop: threading.Event) -> None:
    """Read *stream* until it ends, or until *stop* says it will not.

    A normal command closes its streams when it exits, so the reader reaches
    end-of-file and returns. A descendant can keep the write end open, so on a
    platform with non-blocking descriptors the reader polls and leaves when
    *stop* is set. Elsewhere it blocks, and the caller treats it as a daemon
    rather than waiting for it.
    """
    if os.name == "posix":
        _polled_drain(stream, capture, stop)
        return
    _blocking_drain(stream, capture)


def _spawn(
    argv: list[str], *, cwd: Path, environment: typ.Mapping[str, str]
) -> subprocess.Popen[bytes]:
    """Start *argv* in the complete *environment* the caller supplied."""
    # The declaration names the executable, and it is resolved before this
    # call, so the arguments are not untrusted shell input.
    options: dict[str, object] = {}
    if os.name == "posix":
        # One process group, so a timeout stops the descendants a check
        # started as well as the check itself.
        options["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603
        argv,
        cwd=str(cwd),
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **options,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Stop *process* and its group, then reap it."""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            # The process may already have exited, or never have formed its
            # own group; the direct kill below is still safe.
            process.kill()
    else:
        process.kill()
    process.wait()


def _join_until(threads: typ.Iterable[threading.Thread], deadline: float) -> None:
    """Join each thread, spending at most the time left before *deadline*."""
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _read_streams(
    process: subprocess.Popen[bytes], timeout: int
) -> tuple[tuple[_Capture, _Capture], bool]:
    """Read both streams, keeping each one's first ``LOG_BOUND_BYTES``.

    The bound applies while reading rather than after the command returns, so
    a command that never stops printing cannot grow the preflight's memory.
    A thread per stream keeps both pipes draining, so neither can fill and
    stall the command.

    Returns the captures and whether the command was stopped at *timeout*. A
    stopped command keeps what it printed: a startup error followed by a hang
    is exactly the evidence the finding needs.
    """
    captures = (_Capture(), _Capture())
    stop = threading.Event()
    readers = [
        (
            stream,
            threading.Thread(target=_drain, args=(stream, capture, stop), daemon=True),
        )
        for stream, capture in zip(
            (process.stdout, process.stderr), captures, strict=True
        )
        if stream is not None
    ]
    for _, thread in readers:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop(process)
    finally:
        # A normal command closes its pipes as it exits, so the readers reach
        # end-of-file within the grace period. A descendant can keep the write
        # end open, so once the shared grace is spent the readers are told to
        # stop, and then given only the time a poll needs to notice. One grace
        # period covers every stream, so cleanup is bounded by it however many
        # pipes a descendant holds open.
        _join_until(
            (thread for _, thread in readers),
            time.monotonic() + _DRAIN_GRACE_SECONDS,
        )
        stop.set()
        _join_until(
            (thread for _, thread in readers),
            time.monotonic() + _DRAIN_STOP_SECONDS,
        )
        # Closing a stream a reader still holds would wait for that reader's
        # lock, so a stream is closed only once its reader has finished.
        for stream, thread in readers:
            if not thread.is_alive():
                stream.close()
    return captures, timed_out


def _stream_text(capture: _Capture) -> str:
    """Decode one bounded stream, marking the bytes that were dropped."""
    kept, dropped = capture.snapshot()
    text = kept.decode("utf-8", "replace")
    if not dropped:
        return text
    return f"{text}\n{TRUNCATION_PREFIX}{dropped} bytes truncated]"


def run_command(invocation: Invocation) -> tuple[Ran | None, str]:
    """Run *invocation* with bounded output, or return why it could not run.

    Parameters
    ----------
    invocation : Invocation
        The command, its working directory, the entries layered over the
        ambient environment, and the ceiling on its wall clock.

    Returns
    -------
    tuple[Ran | None, str]
        The bounded result, and the reason the command was stopped or could
        not produce a result. A timed-out command still returns its captured
        streams, with ``timed_out`` set and the reason filled in.

    """
    command = invocation.command
    # The environment is merged before the executable is looked up, so a bare
    # name resolves on the PATH the child will actually receive. Resolving
    # first would let the ambient PATH pick a different executable than the
    # declared one, or report a command missing when the declaration supplies it.
    environment = {**invocation.ambient, **invocation.env}
    executable, problem = resolve(command[0], invocation.cwd, environment)
    if executable is None:
        return None, problem
    try:
        process = _spawn(
            [executable, *command[1:]],
            cwd=invocation.cwd,
            environment=environment,
        )
    except (OSError, ValueError) as error:
        # OSError covers the spawn itself: a path that is not executable, a
        # working directory that cannot be entered, or a full process table.
        # ValueError covers an environment entry the platform cannot pass.
        return None, f"it could not be started: {error}"
    captures, timed_out = _read_streams(process, invocation.timeout)
    stdout, stderr = captures
    outcome = Ran(
        exit_code=int(process.returncode or 0),
        stdout=_stream_text(stdout),
        stderr=_stream_text(stderr),
        timed_out=timed_out,
        stdout_truncated=bool(stdout.dropped),
        stderr_truncated=bool(stderr.dropped),
    )
    if timed_out:
        return outcome, timeout_message(invocation.timeout)
    return outcome, ""
