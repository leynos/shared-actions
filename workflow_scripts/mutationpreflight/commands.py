"""Checking that every required command resolves, at the declared version.

A command the tests shell out to is a prerequisite like any other: naming it
here stops a baseline failure from being attributed to a mutant.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .constants import SKIPPED, VERSION_TIMEOUT_SECONDS
from .models import CommandRequirement, Declaration, PhaseResult, phase_outcome
from .running import (
    bounded,
    last_line,
    reports_version,
    resolution_problem,
    run_command,
)

_NO_COMMANDS: typ.Final[str] = "no require_commands declared"


def _unresolved(name: str, problem: str) -> tuple[str, str]:
    """Return the finding and evidence for a command that does not resolve."""
    finding = (
        f"required command {name!r} {problem}; install it "
        f"before the mutation run, or stop declaring it"
    )
    return finding, problem


def _review_command(
    repo_root: Path, requirement: CommandRequirement
) -> tuple[str | None, object]:
    """Return what one command reported, and the finding when it does not hold.

    An unpinned command is only required to resolve. Asking it for
    ``--version`` without a declared version would impose a version interface
    the declaration never named, and a tool that answers ``--version`` with a
    non-zero exit is not thereby missing.
    """
    if not requirement.version:
        problem = resolution_problem(requirement.name, repo_root)
        if problem:
            return _unresolved(requirement.name, problem)
        return None, {"resolved": requirement.name}
    outcome, problem = run_command(
        (requirement.name, *requirement.version_args),
        cwd=repo_root,
        env={},
        timeout=VERSION_TIMEOUT_SECONDS,
    )
    if outcome is None:
        return _unresolved(requirement.name, problem)
    printed = f"{outcome.stdout}\n{outcome.stderr}"
    evidence: object = bounded(printed.strip())
    if outcome.exit_code != 0:
        # A probe that failed has not established anything, whatever it printed:
        # a version string in the output of a crashing command is not a pass.
        finding = (
            f"required command {requirement.name!r} exited {outcome.exit_code} "
            f"when asked for its version: {last_line(outcome)!r}"
        )
        return finding, evidence
    if requirement.version and not reports_version(printed, requirement.version):
        finding = (
            f"required command {requirement.name!r} does not report "
            f"{requirement.version!r}: it printed {last_line(outcome)!r}"
        )
        return finding, evidence
    return None, evidence


def run_commands_phase(repo_root: Path, declaration: Declaration) -> PhaseResult:
    """Check that every required command resolves, at the declared version.

    Parameters
    ----------
    repo_root : Path
        The working directory each version query runs from.
    declaration : Declaration
        The repository's declaration, whose ``require_commands`` are checked.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``fail`` when a command does not
        resolve or does not report the declared version, ``pass`` otherwise.

    """
    if not declaration.commands:
        return PhaseResult("commands", SKIPPED, (), {"reason": _NO_COMMANDS})
    pairs = [
        (requirement, _review_command(repo_root, requirement))
        for requirement in declaration.commands
    ]
    findings = [finding for _, (finding, _) in pairs if finding is not None]
    detail = {requirement.name: evidence for requirement, (_, evidence) in pairs}
    return phase_outcome("commands", findings, [], detail)
