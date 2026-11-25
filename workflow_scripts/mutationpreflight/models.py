"""The values every phase passes between itself and the envelope.

These are plain data: a declaration, a phase's outcome, one command's bounded
result. Nothing here reads a file or runs a process, so a phase's evidence can
be inspected without repeating the work that produced it.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .constants import FAILED, SUCCEEDED, UNKNOWN


class DeclarationError(ValueError):
    """Raised when a declaration cannot be read as the schema it claims."""


@dc.dataclass(frozen=True, slots=True)
class CommandRequirement:
    """One external command a mutation run needs.

    Attributes
    ----------
    name : str
        The command as it is invoked, resolved through ``PATH``.
    version : str
        Version the command must report, matched as a dotted version token in
        its output. Empty when any version will do.
    version_args : tuple[str, ...]
        Arguments that make the command print its version.

    """

    name: str
    version: str = ""
    version_args: tuple[str, ...] = ("--version",)


@dc.dataclass(frozen=True, slots=True)
class NativeRequirement:
    """A locked native dependency whose interpreter support matters.

    Attributes
    ----------
    package : str
        The distribution name, as ``uv.lock`` records it.
    revision : str
        The revision the lock must pin, matched inside the locked source.
        Empty when the lock's own record is enough.
    supported_python : str
        The PEP 508 range the dependency supports, judged against the project
        interpreter. Empty when support is not established here.
    probe : tuple[str, ...]
        A command that exercises the built extension. Empty when no probe is
        declared.

    """

    package: str
    revision: str = ""
    supported_python: str = ""
    probe: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Check:
    """One bounded command run from a declared boundary.

    Attributes
    ----------
    name : str
        The name the finding uses.
    command : tuple[str, ...]
        The command and its arguments.
    cwd : str
        Working directory, relative to the checkout.
    timeout_seconds : int
        Ceiling on the command's wall clock.
    env : tuple[tuple[str, str], ...]
        Environment entries layered over the ambient one. A relative
        ``PYTHONPATH`` is how a subprocess fixture declares its import root.

    """

    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 300
    env: tuple[tuple[str, str], ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Declaration:
    """A repository's mutation environment requirements, as declared.

    Attributes
    ----------
    source : Path
        The file the declaration was read from.
    lane : str
        The lane the declaration was read for.
    sandbox_paths : tuple[str, ...]
        Paths the mutants sandbox must contain.
    commands : tuple[CommandRequirement, ...]
        External commands the run needs.
    native : NativeRequirement | None
        The native dependency whose support must hold, when declared.
    checks : tuple[Check, ...]
        Bounded commands run before the mutation run.

    """

    source: Path
    lane: str
    sandbox_paths: tuple[str, ...] = ()
    commands: tuple[CommandRequirement, ...] = ()
    native: NativeRequirement | None = None
    checks: tuple[Check, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class Loaded:
    """What reading the declaration produced.

    Attributes
    ----------
    source : Path | None
        The file read, or ``None`` when the repository declares nothing.
    declaration : Declaration | None
        The parsed declaration, or ``None`` when it could not be parsed.
    findings : tuple[str, ...]
        Why the declaration was refused.

    """

    source: Path | None
    declaration: Declaration | None
    findings: tuple[str, ...] = ()


@dc.dataclass(frozen=True, slots=True)
class PhaseResult:
    """One phase's outcome.

    Attributes
    ----------
    name : str
        The phase's stable name, used in the envelope and the summary.
    status : str
        ``pass``, ``fail``, ``skipped`` or ``unknown``.
    findings : tuple[str, ...]
        What the phase found, each naming the path, command or boundary.
    detail : dict[str, object]
        Machine-readable evidence, reported but never inferred from.

    """

    name: str
    status: str
    findings: tuple[str, ...] = ()
    detail: dict[str, object] = dc.field(default_factory=dict)


@dc.dataclass(frozen=True, slots=True)
class Ran:
    """One executed command's bounded result.

    Attributes
    ----------
    exit_code : int
        The command's exit status.
    stdout : str
        Its standard output, truncated at ``LOG_BOUND_BYTES``.
    stderr : str
        Its standard error, truncated at ``LOG_BOUND_BYTES``.
    timed_out : bool
        Whether the command was stopped at its declared ceiling. A timed-out
        result still carries the streams captured up to that point.
    stdout_truncated : bool
        Whether bytes were dropped from standard output, so a token that ends
        at the bound is not mistaken for a complete one.
    stderr_truncated : bool
        Whether bytes were dropped from standard error.

    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dc.dataclass(frozen=True, slots=True)
class Runtimes:
    """The two interpreters a run reports, kept apart on purpose.

    A helper runtime inside its own floor says nothing about the project's
    range, so the two are never collapsed into one figure.

    Attributes
    ----------
    helper : str
        The interpreter running the preflight.
    project : str
        The interpreter the caller selected for the project.

    """

    helper: str
    project: str


@dc.dataclass(frozen=True, slots=True)
class Report:
    """One preflight run, as it is published.

    Attributes
    ----------
    lane : str
        The lane the run belonged to.
    runtimes : Runtimes
        Both interpreters, reported separately.
    results : tuple[PhaseResult, ...]
        Every phase, in the order it ran.
    verdict : str
        ``pass`` unless a phase failed.

    """

    lane: str
    runtimes: Runtimes
    results: tuple[PhaseResult, ...]
    verdict: str


def phase_outcome(
    name: str,
    blocking: list[str],
    indeterminate: list[str],
    detail: dict[str, object],
) -> PhaseResult:
    """Return a phase result ranked blocking, then unknown, then success.

    Parameters
    ----------
    name : str
        The phase's stable name.
    blocking : list[str]
        Findings that must stop the run.
    indeterminate : list[str]
        What the phase could not decide, reported alongside a failure.
    detail : dict[str, object]
        Machine-readable evidence.

    Returns
    -------
    PhaseResult
        A failure when anything blocked, else ``unknown`` when anything was
        undecided, else a pass.

    """
    if blocking:
        return PhaseResult(name, FAILED, (*blocking, *indeterminate), detail)
    if indeterminate:
        return PhaseResult(name, UNKNOWN, tuple(indeterminate), detail)
    return PhaseResult(name, SUCCEEDED, (), detail)
