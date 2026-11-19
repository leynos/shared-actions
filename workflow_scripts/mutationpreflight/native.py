"""Checking a locked native dependency's revision and interpreter support.

Support that cannot be established is reported as unknown, never assumed. The
ABI forward-compatibility escape hatch is recorded for diagnosis and is not
treated as proof that the interpreter is supported.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from .ports import Run

from .constants import FAILED, MAX_CHECK_TIMEOUT_SECONDS, SKIPPED, UNKNOWN
from .interpreters import judge
from .lockfile import (
    find_locked,
    locked_packages,
    pinned_revision,
    source_text,
)
from .models import Declaration, NativeRequirement, PhaseResult, Ran, phase_outcome
from .ports import Invocation, project_environment
from .running import first_diagnostic

_NO_NATIVE: typ.Final[str] = "no native dependency declared"

#: A hexadecimal commit id short enough to be a prefix rather than a complete
#: id. A declaration may name the full commit or any unambiguous prefix.
_COMMIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{4,40}")

#: Why a probe was not run: its verdict would describe an environment the
#: mutation run is not going to use.
_PROBE_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so {package}'s probe was not run: it would "
    "sync the project under the interpreter the phase rejected"
)


def _revision_holds(declared: str, pinned: str) -> bool:
    """Return whether *pinned* is the revision *declared* names."""
    if pinned == declared:
        return True
    return bool(
        _COMMIT_PREFIX.fullmatch(declared)
        and pinned.lower().startswith(declared.lower())
    )


def _revision_finding(
    native: NativeRequirement, package: dict[str, object]
) -> str | None:
    """Return why the lock does not pin the declared revision, or ``None``."""
    if not native.revision:
        return None
    pinned = pinned_revision(package)
    if pinned is None:
        source = source_text(package)
        return (
            f"uv.lock records no pinned revision for {native.package} at "
            f"{source!r}, so the declared revision {native.revision!r} is not "
            f"established"
        )
    if _revision_holds(native.revision, pinned):
        return None
    return (
        f"uv.lock pins {native.package} at revision {pinned!r}, which is not the "
        f"declared revision {native.revision!r}"
    )


def _locked_identity(
    repo_root: Path, native: NativeRequirement
) -> tuple[dict[str, object], list[str], list[str]]:
    """Return the lock's record of *native*, and what that leaves open."""
    packages, problem = locked_packages(repo_root)
    if problem is not None:
        return {}, [], [problem]
    locked = find_locked(packages, native.package)
    if locked is None:
        finding = f"the declared native dependency {native.package!r} is not in uv.lock"
        return {}, [finding], []
    pinned = pinned_revision(locked)
    identity: dict[str, object] = {
        "locked_version": locked.get("version", ""),
        "locked_source": source_text(locked),
        "locked_revision": pinned or "",
    }
    finding = _revision_finding(native, locked)
    if finding is not None:
        return identity, [finding], []
    return identity, [], []


def _support_findings(
    native: NativeRequirement, run: Run
) -> tuple[list[str], list[str]]:
    """Return why the interpreter is out of range, or why support is unknown."""
    project_python = run.runtimes.project
    if not native.supported_python:
        unknown = (
            f"no supported-python range is declared for {native.package}, so "
            f"interpreter compatibility is unknown rather than established"
        )
        return [], [unknown]
    if not project_python:
        unknown = (
            f"no project interpreter was supplied, so {native.package}'s "
            f"declared support {native.supported_python!r} was not checked"
        )
        return [], [unknown]
    return judge(
        run,
        native.supported_python,
        project_python,
        subject=f"{native.package}'s supported-python",
    )


def _probe_detail(
    command: tuple[str, ...], outcome: Ran | None, problem: str
) -> dict[str, object]:
    """Return what a probe reported, bounded, for the envelope."""
    if outcome is None:
        return {"command": list(command), "problem": problem}
    return {
        "command": list(command),
        "exit_code": outcome.exit_code,
        "timed_out": outcome.timed_out,
        "problem": problem,
        "stderr": outcome.stderr,
        "stdout": outcome.stdout,
    }


@dc.dataclass(frozen=True, slots=True)
class Probe:
    """What running, or withholding, a probe established.

    Attributes
    ----------
    blocking : tuple[str, ...]
        Why the probe stops the run: it could not start, timed out, or exited
        non-zero.
    indeterminate : tuple[str, ...]
        Why the probe established nothing without failing: it was withheld
        because an earlier prerequisite failed.
    detail : dict[str, object] | None
        The probe's envelope entry, or ``None`` when none was declared.

    """

    blocking: tuple[str, ...] = ()
    indeterminate: tuple[str, ...] = ()
    detail: dict[str, object] | None = None


def probe_native(run: Run, declaration: Declaration) -> Probe:
    """Run the declared probe under the project interpreter.

    Parameters
    ----------
    run : Run
        The checkout the probe runs in, the runner, and the project
        interpreter's selection.
    declaration : Declaration
        The repository's declaration, whose ``native`` probe is run.

    Returns
    -------
    Probe
        The probe's blocking findings and its evidence. A probe that exceeds
        its ceiling is a failure, and the evidence keeps the reason it was
        stopped, so a stalled build is not reported as a silent failure.

    """
    native = declaration.native
    if native is None or not native.probe:
        return Probe()
    outcome, problem = run.runner(
        Invocation(
            native.probe,
            cwd=run.root,
            env=project_environment(run),
            timeout=MAX_CHECK_TIMEOUT_SECONDS,
            ambient=run.ambient,
        )
    )
    evidence = _probe_detail(native.probe, outcome, problem)
    if outcome is None:
        finding = f"the native probe for {native.package} could not run: {problem}"
        return Probe((finding,), detail=evidence)
    if outcome.timed_out:
        finding = (
            f"the native probe for {native.package} {problem}: "
            f"{first_diagnostic(outcome)}"
        )
        return Probe((finding,), detail=evidence)
    if outcome.exit_code != 0:
        finding = (
            f"the native probe for {native.package} failed with exit code "
            f"{outcome.exit_code}: {first_diagnostic(outcome)}"
        )
        return Probe((finding,), detail=evidence)
    return Probe(detail=evidence)


def probe_blocked(declaration: Declaration) -> Probe:
    """Return the evidence for a probe that must not run.

    A probe is withheld when an earlier prerequisite failed. Running it would
    act under the interpreter the preflight just refused, and its verdict would
    describe an environment the mutation run will not use.

    Parameters
    ----------
    declaration : Declaration
        The repository's declaration, whose ``native`` probe is withheld.

    Returns
    -------
    Probe
        The finding naming the withheld probe, and evidence that records it as
        skipped rather than as a probe nobody attempted.

    """
    native = declaration.native
    if native is None or not native.probe:
        return Probe()
    blocked = _PROBE_BLOCKED.format(package=native.package)
    return Probe((), (blocked,), {"command": list(native.probe), "skipped": blocked})


def with_probe(judged: PhaseResult, probe: Probe) -> PhaseResult:
    """Return *judged* with the probe's findings and evidence folded in.

    Parameters
    ----------
    judged : PhaseResult
        The lock and support verdict, without a probe.
    probe : Probe
        What running or withholding the probe established.

    Returns
    -------
    PhaseResult
        The same phase, with the probe's blocking finding, if any, and its
        evidence recorded. The judged verdict stands when no probe was
        declared.

    """
    if probe.detail is None:
        return judged
    detail = {**judged.detail, "probe": probe.detail}
    # The judged verdict already ranked its own findings; folding them back in
    # with their own rank keeps the combined status the stricter of the two.
    blocking = list(probe.blocking)
    if judged.status == FAILED:
        blocking = [*judged.findings, *blocking]
    indeterminate = list(probe.indeterminate)
    if judged.status == UNKNOWN:
        indeterminate = [*judged.findings, *indeterminate]
    return phase_outcome(judged.name, blocking, indeterminate, detail)


def judge_native(run: Run, declaration: Declaration) -> PhaseResult:
    """Judge the declared native dependency's revision and interpreter range.

    The judgement is a query: it reads the lock and the declaration's range,
    and runs nothing, so it can be shown to leave the checkout untouched. The
    probe that exercises the built extension is composed onto its result by the
    caller.

    Parameters
    ----------
    run : Run
        The checkout whose ``uv.lock`` pins the dependency, the project
        interpreter and the recorded ABI3 setting.
    declaration : Declaration
        The repository's declaration, whose ``native`` table is judged.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the lock or the
        support range could not establish compatibility, ``fail`` when a
        revision or a range does not hold, ``pass`` otherwise.

    """
    native = declaration.native
    if native is None:
        return PhaseResult("native", SKIPPED, (), {"reason": _NO_NATIVE})
    identity: dict[str, object] = {"package": native.package}
    locked_detail, lock_blocking, lock_unknown = _locked_identity(run.root, native)
    identity.update(locked_detail)
    identity["supported_python"] = native.supported_python
    identity["abi3_forward_compatibility"] = run.abi3
    support_blocking, support_unknown = _support_findings(native, run)
    return phase_outcome(
        "native",
        [*lock_blocking, *support_blocking],
        [*lock_unknown, *support_unknown],
        identity,
    )
