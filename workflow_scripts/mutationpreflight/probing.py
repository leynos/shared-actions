"""Running the declared native probe, and folding its result into a verdict.

A probe is a command, not a judgement: it builds or imports the extension under
the project interpreter, and its result is composed onto the verdict the pure
judgement reached. Keeping the two apart is what lets the judgement be tested
as a query that runs nothing.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from .constants import FAILED, MAX_CHECK_TIMEOUT_SECONDS, UNKNOWN
from .diagnostics import first_diagnostic
from .lockfile import find_locked
from .models import Declaration, NativeRequirement, PhaseResult, Ran, phase_outcome
from .native import identity_of, judged_identity
from .ports import Invocation, project_environment

if typ.TYPE_CHECKING:
    from .ports import Run


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


#: Why a probe was not run: its verdict would describe an environment the
#: mutation run is not going to use.
_PROBE_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so {package}'s probe was not run: it would "
    "sync the project under the interpreter the phase rejected"
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


def _probe_findings(
    native: NativeRequirement, outcome: Ran | None, problem: str
) -> list[str]:
    """Return why the probe did not establish the package, if it did not."""
    if outcome is None:
        reason = f"could not run: {problem}"
    elif outcome.timed_out:
        reason = f"{problem}: {first_diagnostic(outcome)}"
    elif outcome.exit_code != 0:
        reason = (
            f"failed with exit code {outcome.exit_code}: {first_diagnostic(outcome)}"
        )
    else:
        return []
    return [f"the native probe for {native.package} {reason}"]


def _relocked_finding(
    run: Run, native: NativeRequirement, judged: PhaseResult
) -> str | None:
    """Return why the probe left a lock the judgement no longer describes.

    The judgement read the lock before the probe ran. A probe that runs the
    project through a tool which may relock it, such as ``uv run`` without
    ``--locked``, could then certify an environment the judgement never saw.
    The lock is read again, and an identity that moved -- version, source or
    revision -- is the probe's failure rather than a success under the old
    record.
    """
    before = judged_identity(judged)
    if before is None:
        # The judgement never established the package's lock identity, so it
        # has nothing the probe could have moved; its own findings stand.
        return None
    packages, problem = run.reader.read_lock_packages(run.root)
    if problem is not None:
        return (
            f"the native probe for {native.package} left uv.lock unreadable: {problem}"
        )
    locked = find_locked(packages, native.package)
    if locked is None:
        return (
            f"the native probe for {native.package} left a uv.lock that no "
            f"longer records the package"
        )
    after = identity_of(locked)
    if after == before:
        return None
    return (
        f"the native probe for {native.package} changed uv.lock from "
        f"{before.render()} to {after.render()}; run the probe with the lock "
        f"enforced (uv's --locked or UV_LOCKED) so it cannot relock the project"
    )


def probe_native(run: Run, declaration: Declaration, judged: PhaseResult) -> Probe:
    """Run the declared probe under the project interpreter.

    Parameters
    ----------
    run : Run
        The checkout the probe runs in, the runner, and the project
        interpreter's selection.
    declaration : Declaration
        The repository's declaration, whose ``native`` probe is run.
    judged : PhaseResult
        The lock and range verdict the probe is composed onto. Its recorded
        revision is what the probe's environment must still hold.

    Returns
    -------
    Probe
        The probe's blocking findings and its evidence. A probe that exceeds
        its ceiling is a failure, and the evidence keeps the reason it was
        stopped, so a stalled build is not reported as a silent failure. A
        probe that leaves a different lock than the one judged is a failure
        too: its verdict would describe an environment the judgement never
        saw.

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
            resolver=run.resolver,
        )
    )
    evidence = _probe_detail(native.probe, outcome, problem)
    findings = _probe_findings(native, outcome, problem)
    if outcome is not None:
        changed = _relocked_finding(run, native, judged)
        if changed is not None:
            findings.append(changed)
    return Probe(tuple(findings), detail=evidence)


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
