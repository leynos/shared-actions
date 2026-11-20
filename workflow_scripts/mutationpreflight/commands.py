"""Checking that every required command resolves, at the declared version.

A command the tests shell out to is a prerequisite like any other: naming it
here stops a baseline failure from being attributed to a mutant.
"""

from __future__ import annotations

import typing as typ

from .constants import SKIPPED, VERSION_TIMEOUT_SECONDS
from .models import CommandRequirement, Declaration, PhaseResult, phase_outcome
from .ports import Invocation, project_environment
from .running import (
    bounded,
    complete_stream,
    last_line,
    reports_version,
    resolution_problem,
)

if typ.TYPE_CHECKING:
    from .ports import Run

_NO_COMMANDS: typ.Final[str] = "no require_commands declared"

_PROBE_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so the version of {name!r} was not probed: "
    "a probe that runs the project would use the interpreter that phase rejected"
)


def _unresolved(name: str, problem: str) -> tuple[str, str]:
    """Return the finding and evidence for a command that cannot run."""
    # A resolution failure already names the command it could not find; a
    # startup failure or a timeout does not, so the name is added only when
    # the reason does not carry it. Otherwise the finding says the name twice.
    if problem.startswith(f"{name!r} "):
        subject = f"required command {problem}"
    else:
        subject = f"required command {name!r} cannot run: {problem}"
    finding = f"{subject}; install it before the mutation run, or stop declaring it"
    return finding, problem


def _resolved_only(
    run: Run, requirement: CommandRequirement
) -> tuple[str | None, object]:
    """Return whether *requirement* resolves, without asking its version."""
    problem = resolution_problem(requirement.name, run.root)
    if problem:
        return _unresolved(requirement.name, problem)
    return None, {"resolved": requirement.name}


def _reported_version(
    run: Run, requirement: CommandRequirement
) -> tuple[str | None, object]:
    """Return what *requirement* reports, and the finding when it does not hold."""
    outcome, problem = run.runner(
        Invocation(
            (requirement.name, *requirement.version_args),
            cwd=run.root,
            env=project_environment(run),
            timeout=VERSION_TIMEOUT_SECONDS,
            ambient=run.ambient,
        )
    )
    if outcome is None:
        return _unresolved(requirement.name, problem)
    # A token cut off at the truncation bound is unproven; complete tokens
    # printed before the flood still count. Only a stream the run dropped bytes
    # from is read that way, so output that merely resembles the marker is kept.
    printed = (
        f"{complete_stream(outcome.stdout, truncated=outcome.stdout_truncated)}\n"
        f"{complete_stream(outcome.stderr, truncated=outcome.stderr_truncated)}"
    )
    evidence: object = bounded(printed.strip())
    if outcome.timed_out:
        finding = (
            f"required command {requirement.name!r} {problem} when asked for "
            f"its version"
        )
        return finding, evidence
    if outcome.exit_code != 0:
        # A probe that failed has not established anything, whatever it printed:
        # a version string in the output of a crashing command is not a pass.
        finding = (
            f"required command {requirement.name!r} exited {outcome.exit_code} "
            f"when asked for its version: {last_line(outcome)!r}"
        )
        return finding, evidence
    if not reports_version(printed, requirement.version):
        finding = (
            f"required command {requirement.name!r} does not report "
            f"{requirement.version!r}: it printed {last_line(outcome)!r}"
        )
        return finding, evidence
    return None, evidence


def _review_command(
    run: Run, requirement: CommandRequirement, *, probe_versions: bool
) -> tuple[str | None, object]:
    """Return what one command reported, and the finding when it does not hold.

    An unpinned command is only required to resolve. Asking it for
    ``--version`` without a declared version would impose a version interface
    the declaration never named, and a tool that answers ``--version`` with a
    non-zero exit is not thereby missing.
    """
    if not requirement.version:
        return _resolved_only(run, requirement)
    if not probe_versions:
        return _resolved_only(run, requirement)
    return _reported_version(run, requirement)


def run_commands_phase(
    run: Run, declaration: Declaration, *, probe_versions: bool = True
) -> PhaseResult:
    """Check that every required command resolves, at the declared version.

    Parameters
    ----------
    run : Run
        The checkout each version query runs from, the runner, and the ambient
        environment.
    declaration : Declaration
        The repository's declaration, whose ``require_commands`` are checked.
    probe_versions : bool
        Whether a pinned command may be asked for its version. False when the
        interpreter phase failed, because a probe such as ``uv run python``
        would act under the interpreter that phase rejected; resolution is
        still checked.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``fail`` when a command does not
        resolve or does not report the declared version, ``unknown`` when a
        pinned command's version was not probed, ``pass`` otherwise.

    """
    if not declaration.commands:
        return PhaseResult("commands", SKIPPED, (), {"reason": _NO_COMMANDS})
    pairs = [
        (
            requirement,
            _review_command(run, requirement, probe_versions=probe_versions),
        )
        for requirement in declaration.commands
    ]
    findings = [finding for _, (finding, _) in pairs if finding is not None]
    detail: dict[str, object] = {
        "results": [
            {
                "index": index,
                "name": requirement.name,
                "evidence": evidence,
            }
            for index, (requirement, (_, evidence)) in enumerate(pairs)
        ]
    }
    if probe_versions:
        return phase_outcome("commands", findings, [], detail)
    # A pin nobody probed was not established. The phase says so per command,
    # so the status matches the native phase's rule for support it could not
    # judge, and the finding names the command rather than a placeholder.
    unprobed = [
        _PROBE_BLOCKED.format(name=item.name)
        for item in declaration.commands
        if item.version
    ]
    return phase_outcome("commands", findings, unprobed, detail)
