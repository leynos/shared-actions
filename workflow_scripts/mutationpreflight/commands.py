"""Checking that every required command resolves, at the declared version.

A command the tests shell out to is a prerequisite like any other: naming it
here stops a baseline failure from being attributed to a mutant. Resolution is
a query, answered by the resolver alone; establishing a pinned version is a
command, and is named as one wherever it is chosen.
"""

from __future__ import annotations

import typing as typ

from .constants import SKIPPED, VERSION_TIMEOUT_SECONDS
from .diagnostics import bounded, complete_stream, last_line, reports_version
from .models import CommandRequirement, Declaration, PhaseResult, phase_outcome
from .ports import Invocation, project_environment

if typ.TYPE_CHECKING:
    from .ports import CommandResolver, Runner, Settings

_NO_COMMANDS: typ.Final[str] = "no require_commands declared"

_PROBE_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so the version of {name!r} was not probed: "
    "a probe that runs the project would use the interpreter that phase rejected"
)

#: One command's finding and the evidence the check left behind.
_Review = tuple[str | None, object]


def _unresolved(name: str, problem: str) -> _Review:
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
    settings: Settings, resolver: CommandResolver, requirement: CommandRequirement
) -> _Review:
    """Return whether *requirement* resolves, without asking its version."""
    _, problem = resolver(requirement.name, settings.root, settings.ambient)
    if problem:
        return _unresolved(requirement.name, problem)
    return None, {"resolved": requirement.name}


def _version_probe(
    settings: Settings,
    resolver: CommandResolver,
    runner: Runner,
    requirement: CommandRequirement,
) -> _Review:
    """Return what *requirement* reports, and the finding when it does not hold."""
    outcome, problem = runner(
        Invocation(
            (requirement.name, *requirement.version_args),
            cwd=settings.root,
            env=project_environment(settings),
            timeout=VERSION_TIMEOUT_SECONDS,
            ambient=settings.ambient,
            resolver=resolver,
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


def _commands_result(
    declaration: Declaration,
    pairs: list[tuple[CommandRequirement, _Review]],
    *,
    probed: bool,
) -> PhaseResult:
    """Return the commands phase for what establishing each command reported."""
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
    if probed:
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


def resolve_commands(
    settings: Settings, resolver: CommandResolver, declaration: Declaration
) -> PhaseResult:
    """Check that every required command resolves, running nothing.

    This is the query half of the commands phase: the resolver answers whether
    a declared command would run, and no process is started. A pinned version
    is therefore reported as not established rather than assumed.

    Parameters
    ----------
    settings : Settings
        The checkout a relative command is anchored at, and the ambient
        environment a bare name is looked up in.
    resolver : CommandResolver
        How a declared command's executable is established.
    declaration : Declaration
        The repository's declaration, whose ``require_commands`` are checked.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``fail`` when a command does not
        resolve, ``unknown`` when a pinned command's version was not probed,
        ``pass`` otherwise.

    """
    if not declaration.commands:
        return PhaseResult("commands", SKIPPED, (), {"reason": _NO_COMMANDS})
    pairs = [
        (requirement, _resolved_only(settings, resolver, requirement))
        for requirement in declaration.commands
    ]
    return _commands_result(declaration, pairs, probed=False)


def probe_versions(
    settings: Settings,
    resolver: CommandResolver,
    runner: Runner,
    declaration: Declaration,
) -> PhaseResult:
    """Check every required command resolves, running each pinned one.

    Unlike ``resolve_commands`` this is a command: every pinned requirement is
    started with its declared version arguments, so the version it reports is
    the one the mutation run will receive.

    Parameters
    ----------
    settings : Settings
        The checkout each probe runs from, and the ambient environment.
    resolver : CommandResolver
        How a declared command's executable is established.
    runner : Runner
        How a declared command is started.
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
        (
            requirement,
            _version_probe(settings, resolver, runner, requirement)
            if requirement.version
            else _resolved_only(settings, resolver, requirement),
        )
        for requirement in declaration.commands
    ]
    return _commands_result(declaration, pairs, probed=True)
