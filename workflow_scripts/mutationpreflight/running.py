"""Running one declared command with bounded output.

Every command the preflight runs is bounded in both output and wall clock, so a
noisy or hung prerequisite is a named finding rather than an unreadable log or
a job that never finishes.
"""

from __future__ import annotations

import dataclasses as dc
import os
import subprocess
import threading
import typing as typ
from pathlib import Path

from plumbum import CommandNotFound, local

from .constants import LOG_BOUND_BYTES, VERSION_TOKEN
from .models import Ran

#: How a bounded stream marks the bytes it dropped. ``last_line`` ignores it,
#: so a truncated stream still names a line of its retained output.
TRUNCATION_PREFIX: typ.Final[str] = "[... "

#: How much of one stream is read at a time.
_READ_CHUNK_BYTES: typ.Final[int] = 8192


class _RunError(Exception):
    """A command that produced no bounded result."""


@dc.dataclass
class _Capture:
    """One stream's retained prefix, and how many bytes were dropped."""

    kept: bytearray = dc.field(default_factory=bytearray)
    dropped: int = 0

    def take(self, chunk: bytes) -> None:
        """Keep what still fits inside the bound, and count the remainder."""
        room = max(0, LOG_BOUND_BYTES - len(self.kept))
        kept = chunk[:room]
        self.kept.extend(kept)
        self.dropped += len(chunk) - len(kept)


def _drain(stream: typ.IO[bytes], capture: _Capture) -> None:
    """Read *stream* to its end, keeping only the captured prefix."""
    while chunk := stream.read(_READ_CHUNK_BYTES):
        capture.take(chunk)


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

    The anchor is lexical: a virtual environment's ``bin/python`` is usually a
    symlink, and an interpreter finds its own ``site-packages`` from the path it
    was started with. Following the link here would run the target interpreter
    under the link's path and lose the environment the declaration named.
    """
    if not _is_path(name) or Path(name).is_absolute():
        return name
    return os.path.normpath(f"{cwd}{os.sep}{name}")


def _unresolved(name: str, cwd: Path) -> str:
    """Return why *name* cannot be looked up, in the terms its declaration used."""
    if _is_path(name):
        return f"{name!r} is not an executable file in {str(cwd)!r}"
    return f"{name!r} is not on PATH"


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
    try:
        local[_executable(name, cwd)]
    except CommandNotFound:
        return _unresolved(name, cwd)
    return ""


def _timeout_message(timeout: int) -> str:
    """Return why a command was stopped, in the units its declaration used."""
    unit = "second" if timeout == 1 else "seconds"
    return f"it did not finish within {timeout} {unit}"


def _spawn(
    argv: list[str], *, cwd: Path, env: typ.Mapping[str, str]
) -> subprocess.Popen[bytes]:
    """Start *argv*, layering *env* over the ambient environment."""
    # The declaration names the executable, and it is resolved through PLUMBUM
    # before this call, so the arguments are not untrusted shell input.
    return subprocess.Popen(  # noqa: S603
        argv,
        cwd=str(cwd),
        env={**os.environ, **env},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_streams(
    process: subprocess.Popen[bytes], timeout: int
) -> tuple[_Capture, _Capture]:
    """Read both streams, keeping each one's first ``LOG_BOUND_BYTES``.

    The bound applies while reading rather than after the command returns, so
    a command that never stops printing cannot grow the preflight's memory.
    A thread per stream keeps both pipes draining, so neither can fill and
    stall the command.
    """
    captures = (_Capture(), _Capture())
    threads = [
        threading.Thread(target=_drain, args=(stream, capture), daemon=True)
        for stream, capture in zip(
            (process.stdout, process.stderr), captures, strict=True
        )
        if stream is not None
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise _RunError(_timeout_message(timeout)) from None
    finally:
        for thread in threads:
            thread.join(timeout=timeout)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return captures


def _stream_text(capture: _Capture) -> str:
    """Decode one bounded stream, marking the bytes that were dropped."""
    text = bytes(capture.kept).decode("utf-8", "replace")
    if not capture.dropped:
        return text
    return f"{text}\n{TRUNCATION_PREFIX}{capture.dropped} bytes truncated]"


def run_command(
    command: tuple[str, ...], *, cwd: Path, env: typ.Mapping[str, str], timeout: int
) -> tuple[Ran | None, str]:
    """Run *command* with bounded output, or return why it could not run.

    Parameters
    ----------
    command : tuple[str, ...]
        The command and its arguments.
    cwd : Path
        The working directory the command is given.
    env : Mapping[str, str]
        Entries layered over the ambient environment.
    timeout : int
        Ceiling on the command's wall clock, in seconds.

    Returns
    -------
    tuple[Ran | None, str]
        The bounded result, or ``None`` and the reason the command did not
        produce one.

    """
    try:
        executable = local[_executable(command[0], cwd)].executable
    except CommandNotFound:
        return None, _unresolved(command[0], cwd)
    try:
        process = _spawn([str(executable), *command[1:]], cwd=cwd, env=env)
    except OSError as error:
        # OSError covers the spawn itself: a path that is not executable, a
        # working directory that cannot be entered, or a full process table.
        return None, f"it could not be started: {error}"
    try:
        stdout, stderr = _read_streams(process, timeout)
    except _RunError as failure:
        return None, str(failure)
    return (
        Ran(
            exit_code=int(process.returncode or 0),
            stdout=_stream_text(stdout),
            stderr=_stream_text(stderr),
        ),
        "",
    )


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
