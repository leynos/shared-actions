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
import typing as typ
from pathlib import Path

from plumbum import CommandNotFound, local

from .constants import LOG_BOUND_BYTES, VERSION_TOKEN
from .models import Ran

if typ.TYPE_CHECKING:
    from .ports import Invocation

#: How a bounded stream marks the bytes it dropped. ``last_line`` ignores it,
#: so a truncated stream still names a line of its retained output.
TRUNCATION_PREFIX: typ.Final[str] = "[... "

#: How much of one stream is read at a time.
_READ_CHUNK_BYTES: typ.Final[int] = 8192

#: How long a drain thread may keep reading after the command is gone. A
#: descendant that inherited the pipe can outlive the command, so the reader is
#: bounded rather than joined forever.
_DRAIN_GRACE_SECONDS: typ.Final[int] = 5

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


def bounded(text: str) -> str:
    """Return at most ``LOG_BOUND_BYTES`` bytes of *text*, marking the rest.

    Parameters
    ----------
    text : str
        The stream to bound.

    Returns
    -------
    str
        *text* unchanged when it fits, else the kept prefix and a line naming
        how many bytes were dropped.

    """
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= LOG_BOUND_BYTES:
        return text
    kept = encoded[:LOG_BOUND_BYTES].decode("utf-8", "ignore")
    dropped = len(encoded) - LOG_BOUND_BYTES
    return f"{kept}\n{TRUNCATION_PREFIX}{dropped} bytes truncated]"


def _diagnostic_lines(text: str) -> list[str]:
    """Return the meaningful lines of *text*, without the truncation marker.

    Leading whitespace is kept: it is what tells a compiler's first diagnostic
    apart from the traceback frames indented beneath it.
    """
    present = [line.rstrip() for line in text.splitlines() if line.strip()]
    return [line for line in present if not line.strip().startswith(TRUNCATION_PREFIX)]


def last_line(outcome: Ran) -> str:
    """Return the last non-empty line a command printed, for a one-line finding.

    Parameters
    ----------
    outcome : Ran
        The command's bounded result.

    Returns
    -------
    str
        The last non-empty line of standard error, else of standard output,
        else a phrase saying the command printed nothing.

    """
    for text in (outcome.stderr, outcome.stdout):
        printed = _diagnostic_lines(text)
        if printed:
            return printed[-1].strip()
    return "it printed nothing"


def first_diagnostic(outcome: Ran) -> str:
    """Return the first non-empty diagnostic line a command printed.

    A compiler reports the failure that stopped it on its first line and
    context afterwards, so a finding that quotes the last line quotes the
    least useful one.

    Parameters
    ----------
    outcome : Ran
        The command's bounded result.

    Returns
    -------
    str
        The first non-empty line of standard error, else of standard output,
        else a phrase saying the command printed nothing.

    """
    for text in (outcome.stderr, outcome.stdout):
        printed = _diagnostic_lines(text)
        if not printed:
            continue
        # A compiler's first diagnostic begins at the margin. A traceback's
        # first line is its header and its frames are indented, so the line
        # that names the failure is the first unindented one after them.
        leading = [
            line
            for line in printed
            if not line.startswith("Traceback") and not line[:1].isspace()
        ]
        return (leading or printed)[0].strip()
    return "it printed nothing"


def _is_path(name: str) -> bool:
    """Return whether *name* is a path rather than a bare command name."""
    separators = [separator for separator in (os.sep, os.altsep) if separator]
    return any(separator in name for separator in separators)


def _executable(name: str, cwd: Path) -> str:
    """Return the name to look up, anchored at *cwd* when it is a relative path.

    A bare name is resolved through ``PATH``. A name carrying a path separator
    would otherwise be resolved against the process's working directory, which
    is not the directory the command is about to run in.

    The anchor keeps the declaration's own parent components. Normalising them
    away would interpret ``..`` before the operating system follows a symlink
    in the path, so ``link/../probe`` would name a sibling of the link rather
    than a sibling of its target. The path is passed through as written, and
    the operating system resolves it.

    The anchor is also lexical in the sense that the final link is not
    followed: a virtual environment's ``bin/python`` is usually a symlink, and
    an interpreter finds its own ``site-packages`` from the path it was started
    with. Following the link here would run the target interpreter under the
    link's path and lose the environment the declaration named.
    """
    if not _is_path(name) or Path(name).is_absolute():
        return name
    return str(Path(cwd) / name)


def _unresolved(name: str, cwd: Path) -> str:
    """Return why *name* cannot be looked up, in the terms its declaration used."""
    if _is_path(name):
        return f"{name!r} is not an executable file in {str(cwd)!r}"
    return f"{name!r} is not on PATH"


def _resolve(name: str, cwd: Path) -> tuple[str | None, str]:
    """Return the executable *name* resolves to, or why it does not.

    An explicit path is checked as a file the operating system may execute,
    following a symlink to its target, without running it. Only a bare name is
    looked up through ``PATH``.
    """
    if _is_path(name):
        candidate = Path(_executable(name, cwd))
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return None, _unresolved(name, cwd)
        return str(candidate), ""
    try:
        return local[name].executable, ""
    except CommandNotFound:
        return None, _unresolved(name, cwd)


def resolution_problem(name: str, cwd: Path) -> str:
    """Return why *name* cannot be resolved, or ``""`` when it can.

    Parameters
    ----------
    name : str
        The command name or path to look up.
    cwd : Path
        The directory a relative path is anchored at.

    Returns
    -------
    str
        The reason the name does not resolve, or an empty string when it does.

    """
    _, problem = _resolve(name, cwd)
    return problem


def timeout_message(timeout: int) -> str:
    """Return why a command was stopped, in the units its declaration used.

    Parameters
    ----------
    timeout : int
        The ceiling the declaration set, in seconds.

    Returns
    -------
    str
        A phrase naming the ceiling, with the unit agreeing with it.

    """
    unit = "second" if timeout == 1 else "seconds"
    return f"it did not finish within {timeout} {unit}"


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
        # end open, so once the grace is spent the readers are told to stop
        # rather than the preflight waiting on a writer it cannot reach.
        for _, thread in readers:
            thread.join(timeout=_DRAIN_GRACE_SECONDS)
        stop.set()
        for _, thread in readers:
            thread.join(timeout=_DRAIN_GRACE_SECONDS)
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


def complete_stream(text: str) -> str:
    """Return *text* without the token a truncation may have cut in half.

    A token that ends at the bound is unproven: the next dropped byte could
    have been the digit that would have made it a different release. Complete
    tokens earlier in the stream are kept, so a version printed before a flood
    still counts.
    """
    head, separator, _ = text.partition(TRUNCATION_PREFIX)
    if not separator:
        return text
    head = head.rstrip("\n")
    words = head.split()
    if not words:
        return ""
    return head[: len(head) - len(words[-1])]


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
    executable, problem = _resolve(command[0], invocation.cwd)
    if executable is None:
        return None, problem
    try:
        process = _spawn(
            [executable, *command[1:]],
            cwd=invocation.cwd,
            environment={**invocation.ambient, **invocation.env},
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


def reports_version(printed: str, wanted: str) -> bool:
    """Return whether *printed* reports *wanted* as a whole version token.

    A substring test would accept ``2.4`` for ``2.41.0``, which is the kind of
    near-miss this phase exists to catch, so the declared version must equal a
    dotted token in the output or be its dotted prefix: ``2.4`` accepts
    ``2.4.0`` and rejects ``2.41.0``. A pre- or post-release suffix stays in the
    token and makes it a different release, so none of ``1.2.3rc1``,
    ``1.2.3.post1`` or ``1.2.3-rc1`` reports the stable ``1.2.3``; a
    declaration that pins the suffixed release itself, such as ``0.9.0rc1``,
    still matches it exactly.

    Parameters
    ----------
    printed : str
        What the command wrote to both streams, joined.
    wanted : str
        The version the declaration requires.

    Returns
    -------
    bool
        Whether a token in *printed* equals *wanted* or extends it by dotted
        components.

    """
    for token in VERSION_TOKEN.findall(printed):
        if token == wanted:
            return True
        # A suffix makes the token a different release from the stable version
        # it resembles, whatever the dotted components in front of it say.
        if not all(part.isdigit() for part in token.split(".")):
            continue
        if token.startswith(f"{wanted}."):
            return True
    return False
