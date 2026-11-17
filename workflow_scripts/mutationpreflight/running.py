"""Running one declared command with bounded output.

Every command the preflight runs is bounded in both output and wall clock, so a
noisy or hung prerequisite is a named finding rather than an unreadable log or
a job that never finishes.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

from plumbum import CommandNotFound, ProcessExecutionError, ProcessTimedOut, local

from .constants import LOG_BOUND_BYTES, VERSION_TOKEN
from .models import Ran

#: How a bounded stream marks the bytes it dropped. ``last_line`` ignores it,
#: so a truncated stream still names a line of its retained output.
TRUNCATION_PREFIX: typ.Final[str] = "[... "


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
    """Return the meaningful lines of *text*, without the truncation marker."""
    present = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in present if not line.startswith(TRUNCATION_PREFIX)]


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
            return printed[-1]
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
        binary = local[_executable(command[0], cwd)]
    except CommandNotFound:
        if _is_path(command[0]):
            return None, f"{command[0]!r} is not an executable file in {str(cwd)!r}"
        return None, f"{command[0]!r} is not on PATH"
    try:
        completed = binary.run(
            list(command[1:]),
            retcode=None,
            timeout=timeout,
            cwd=str(cwd),
            env=dict(env),
        )
    except ProcessTimedOut:
        unit = "second" if timeout == 1 else "seconds"
        return None, f"it did not finish within {timeout} {unit}"
    except (OSError, ProcessExecutionError) as error:
        # OSError covers the spawn itself: a path that is not executable, a
        # working directory that cannot be entered, or a full process table.
        return None, f"it could not be started: {error}"
    return (
        Ran(
            exit_code=int(completed[0]),
            stdout=bounded(str(completed[1])),
            stderr=bounded(str(completed[2])),
        ),
        "",
    )


def reports_version(printed: str, wanted: str) -> bool:
    """Return whether *printed* reports *wanted* as a whole version token.

    A substring test would accept ``2.4`` for ``2.41.0``, which is the kind of
    near-miss this phase exists to catch, so the declared version must equal a
    dotted token in the output or be its dotted prefix: ``2.4`` accepts
    ``2.4.0`` and rejects ``2.41.0``. A pre- or post-release suffix stays in the
    token and makes it a different release, so neither ``1.2.3rc1`` nor
    ``1.2.3.post1`` reports the stable ``1.2.3``; a declaration that pins the
    suffixed release itself, such as ``0.9.0rc1``, still matches it exactly.

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
