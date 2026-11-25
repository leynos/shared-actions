"""Sequencing the phases, and the policy that decides what runs when.

Every phase is emitted as it finishes, so a crash in a later phase still leaves
the earlier verdicts in the log. What the run publishes is written by
``publication.py``; this module decides the order and the gates.
"""

from __future__ import annotations

import typing as typ

from .check_boundary import run_checks_phase
from .commands import probe_versions, resolve_commands
from .constants import FAILED, SKIPPED, SUCCEEDED
from .interpreters import run_interpreter_phase
from .models import Declaration, Loaded, PhaseResult
from .native import (
    baseline_not_established,
    changed_lock_phase,
    judge_native,
    lock_identity,
    lock_moved,
)
from .probing import probe_blocked, probe_native, with_probe
from .sandbox import run_sandbox_phase

if typ.TYPE_CHECKING:
    from .ports import Run

_NO_DECLARATION: typ.Final[str] = (
    "the repository declares no mutation environment requirements, so "
    "only the interpreter was checked"
)

_REFUSED_DECLARATION: typ.Final[str] = (
    "the declaration at {source} was refused, so no requirement it names was checked"
)

#: The phases a parsed declaration enables, in the order they run. The native
#: judgement runs last, after everything that can rewrite the checkout: the
#: declared checks run there on the cargo lane, so a lock they moved must be
#: seen by the judgement rather than judged before them. A run with no accepted
#: declaration reports each phase as skipped, so a report never omits a phase
#: because the declaration that would have named it was absent.
_DECLARATION_PHASES: typ.Final[tuple[str, ...]] = (
    "sandbox",
    "commands",
    "checks",
    "native",
)

_CHECKS_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so the declared checks were not run: they "
    "would execute the project under the interpreter that phase rejected"
)


def manifest_phase(loaded: Loaded) -> PhaseResult:
    """Return the manifest phase for what reading the declaration produced.

    Parameters
    ----------
    loaded : Loaded
        What reading the declaration produced: its source, the parsed
        declaration when one was accepted, and why it was refused otherwise.

    Returns
    -------
    PhaseResult
        ``skipped`` when the repository declares nothing, ``fail`` when the
        declaration was refused, ``pass`` otherwise.

    """
    if loaded.source is None:
        return PhaseResult("manifest", SKIPPED, (), {"reason": _NO_DECLARATION})
    detail: dict[str, object] = {"source": str(loaded.source)}
    if loaded.findings:
        return PhaseResult("manifest", FAILED, loaded.findings, detail)
    return PhaseResult("manifest", SUCCEEDED, (), detail)


def verdict(results: typ.Sequence[PhaseResult]) -> str:
    """Return the run's verdict: any failure fails the run.

    Parameters
    ----------
    results : Sequence[PhaseResult]
        Every phase that ran, in order.

    Returns
    -------
    str
        ``fail`` when any phase failed, else ``pass``.

    """
    if any(result.status == FAILED for result in results):
        return FAILED
    return SUCCEEDED


def _skipped_phases(loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield the declaration phases as skipped, naming why none of them ran.

    Without this, a repository that declares nothing would produce a report
    mentioning only the manifest and the interpreter: a reader could not tell
    whether the missing phases passed, failed, or were never applicable.
    """
    if loaded.source is None:
        reason = _NO_DECLARATION
    else:
        reason = _REFUSED_DECLARATION.format(source=loaded.source)
    for name in _DECLARATION_PHASES:
        yield PhaseResult(name, SKIPPED, (), {"reason": reason})


def _declaration_phases(
    run: Run, declaration: Declaration, *, interpreter_ok: bool
) -> typ.Iterator[PhaseResult]:
    """Yield the phases a parsed declaration enables, running each when reached.

    The phases that execute the project's own environment are gated on the
    interpreter verdict: a probe or a check run under a rejected interpreter
    would certify an environment the mutation run will not use.

    The native judgement runs last. The declared checks run in the checkout on
    the cargo lane, after the lock was read and before the mutation run starts,
    so a check that relocked the project would leave an earlier judgement
    describing an environment the run no longer uses. The identity the lock held
    before the checks is therefore captured first and compared with the identity
    the judgement reads after them: a lock that moved fails the native phase
    rather than certifying a pin the run will not use. The probe is composed
    onto that verdict only where the interpreter it would run under was
    accepted, and the judgement is passed to it so the lock it ran against is
    the one the probe must leave behind.
    """
    native = declaration.native
    # A baseline the lock could not establish is kept with its reason. It is
    # not a change, but it is not a pass either: a later successful read must
    # not certify a pin the run never established before the checks ran.
    before, baseline_problem = lock_identity(run.ports.lock, run.settings.root, native)
    yield run_sandbox_phase(
        run.ports.configuration, run.ports.paths, run.settings, declaration
    )
    commands = (
        probe_versions(run.settings, run.ports.resolver, run.ports.runner, declaration)
        if interpreter_ok
        else resolve_commands(run.settings, run.ports.resolver, declaration)
    )
    yield commands
    if interpreter_ok:
        yield run_checks_phase(run, declaration)
    else:
        yield PhaseResult("checks", SKIPPED, (), {"reason": _CHECKS_BLOCKED})
    judged = judge_native(run.ports.lock, run.settings, declaration)
    if native is not None and lock_moved(before, judged.identity):
        yield changed_lock_phase(judged.phase, native, before, judged.identity)
        return
    verdict = judged.phase
    if baseline_problem:
        verdict = baseline_not_established(verdict, baseline_problem)
    probe = (
        probe_native(run, declaration, judged)
        if interpreter_ok
        else probe_blocked(declaration)
    )
    yield with_probe(verdict, probe)


def _all_phases(run: Run, loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield every phase in order, running each only when it is reached."""
    yield manifest_phase(loaded)
    interpreter = run_interpreter_phase(run.ports.documents, run.settings)
    yield interpreter
    if loaded.declaration is None:
        yield from _skipped_phases(loaded)
        return
    yield from _declaration_phases(
        run, loaded.declaration, interpreter_ok=interpreter.status != FAILED
    )


def iter_phases(run: Run, loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield every phase as it finishes.

    Parameters
    ----------
    run : Run
        The checkout, both interpreters, the output directory and the selected
        mutmut release.
    loaded : Loaded
        What reading the declaration produced.

    Yields
    ------
    PhaseResult
        Each phase that ran, in order. The caller publishes it; this module
        only decides the order and the gates.

    """
    yield from _all_phases(run, loaded)
