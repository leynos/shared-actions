"""Running one declared command with bounded output.

Every command the preflight runs is bounded in both output and wall clock, so a
noisy or hung prerequisite is a named finding rather than an unreadable log or
a job that never finishes.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from plumbum import CommandNotFound, ProcessExecutionError, ProcessTimedOut, local

from .constants import LOG_BOUND_BYTES, VERSION_TOKEN
from .models import Ran


def bounded(text: str) -> str:
    """Return at most ``LOG_BOUND_BYTES`` bytes of *text*, marking the rest."""
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= LOG_BOUND_BYTES:
        return text
    kept = encoded[:LOG_BOUND_BYTES].decode("utf-8", "ignore")
    dropped = len(encoded) - LOG_BOUND_BYTES
    return f"{kept}\n[... {dropped} bytes truncated]"


def last_line(outcome: Ran) -> str:
    """Return the last non-empty line a command printed, for a one-line finding."""
    for text in (outcome.stderr, outcome.stdout):
        printed = [line.strip() for line in text.splitlines() if line.strip()]
        if printed:
            return printed[-1]
    return "it printed nothing"


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
        binary = local[command[0]]
    except CommandNotFound:
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
        return None, f"it did not finish within {timeout} seconds"
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
    ``2.4.0`` and rejects ``2.41.0``.
    """
    for token in VERSION_TOKEN.findall(printed):
        if token == wanted or token.startswith(f"{wanted}."):
            return True
    return False
