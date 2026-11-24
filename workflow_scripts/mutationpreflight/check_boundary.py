"""Choosing the tree a declared check runs in, and owning its lifecycle.

The cargo lane copies the whole working tree, so its checks run in the
checkout. The mutmut lane copies a filtered set, so its checks run in a fresh
copy built from the selected release's contract — the same files the mutation
run will receive. Selecting that boundary, and removing it again, is kept apart
from running the checks and writing their logs.
"""

from __future__ import annotations

import sys
import typing as typ

from .checks import Boundary, Review, log_stem, run_checks
from .console import emit
from .constants import FAILED, SKIPPED, UNKNOWN
from .copy_set import CopyContract, copy_entry_problems, mutmut_copy_set
from .models import Declaration, PhaseResult, phase_outcome

if typ.TYPE_CHECKING:
    from pathlib import Path

    from .ports import Run

_NO_CHECKS: typ.Final[str] = "no checks declared"
_WHOLE_TREE: typ.Final[str] = "the unmodified checkout, which this lane copies whole"
_NO_SANDBOX: typ.Final[str] = (
    "the checks' sandbox could not be established, so a check that passes "
    "here would not have been shown to survive into the mutation run"
)


def _checks_contract(
    run: Run, declaration: Declaration
) -> CopyContract | PhaseResult | None:
    """Return the copy contract the checks need, or the phase that refuses them.

    ``None`` means the lane copies the whole tree, so the checkout itself is
    the representative sandbox; a contract means a fresh copy has to be
    built. Deciding this reads the configuration and writes nothing.
    """
    if declaration.lane != "mutmut":
        return None
    table, problem = run.reader.read_mutmut_configuration(run.root)
    invalid = copy_entry_problems(table)
    if invalid:
        # An escaping copy entry would put the sandbox somewhere the checks
        # must not run, so it is refused before anything is materialized.
        return PhaseResult("checks", FAILED, invalid, {"reason": _NO_SANDBOX})
    contract, problems = mutmut_copy_set(run.mutmut_version, table, problem)
    if contract is None:
        return PhaseResult("checks", UNKNOWN, problems, {"reason": _NO_SANDBOX})
    return contract


def _materialized_boundary(
    run: Run, contract: CopyContract | None
) -> PhaseResult | Boundary:
    """Return the tree the checks run in, building it when a contract needs one.

    On the mutmut lane this creates the sandbox and marks the boundary as owned
    by the run, so the caller removes it; on the cargo lane it returns the
    checkout, which the run does not own.
    """
    if contract is None:
        return Boundary(run.root, _WHOLE_TREE, "checkout", owned=False)
    sandbox, problems = run.sandbox.materialize(run.root, contract)
    if sandbox is None:
        return PhaseResult("checks", FAILED, problems, {"reason": _NO_SANDBOX})
    return Boundary(sandbox, str(sandbox), "sandbox", owned=True)


def run_checks_phase(run: Run, declaration: Declaration) -> PhaseResult:
    """Run each declared check from the boundary it declares.

    Parameters
    ----------
    run : Run
        The checkout the declaration belongs to, the selected mutmut release,
        the runner, the ambient environment and the log directory.
    declaration : Declaration
        The repository's declaration, whose ``checks`` are run.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the selected
        release's copy contract is not established, ``fail`` when the checkout
        cannot be copied into a sandbox or a check cannot run, times out or
        exits non-zero, ``pass`` otherwise. An I/O failure while building a
        known sandbox is a failure rather than an unknown: nothing was checked,
        and a run whose checks never ran has not passed.

    """
    if not declaration.checks:
        return PhaseResult("checks", SKIPPED, (), {"reason": _NO_CHECKS})
    contract = _checks_contract(run, declaration)
    if isinstance(contract, PhaseResult):
        return contract
    boundary = _materialized_boundary(run, contract)
    if isinstance(boundary, PhaseResult):
        return boundary
    try:
        reviews, results = run_checks(run, declaration, boundary)
        findings = _write_logs(run, declaration, reviews)
        detail: dict[str, object] = {"ran_in": boundary.where, "results": results}
        return phase_outcome("checks", findings, [], detail)
    finally:
        if boundary.owned:
            _discard(run, boundary.root)


def _write_logs(
    run: Run, declaration: Declaration, reviews: typ.Sequence[Review]
) -> list[str]:
    """Write each review's streams, returning the findings that failed a check."""
    findings: list[str] = []
    pairs = zip(declaration.checks, reviews, strict=True)
    for index, (check, review) in enumerate(pairs):
        finding = review.finding
        if review.outcome is not None:
            log_problem = run.evidence.write_check_logs(
                run.output_dir / "checks",
                log_stem(index, check.name),
                check.name,
                review.outcome,
            )
            if log_problem is not None:
                # Evidence that cannot be written is a failure of the check's
                # own report, whatever the command itself exited with.
                finding = log_problem
        if finding is not None:
            findings.append(finding)
    return findings


def _discard(run: Run, sandbox: Path) -> None:
    """Remove a sandbox the run built, reporting a tree that survives.

    The checks' own findings have already been decided, so this failure is
    published as its own finding rather than replacing one. A tree that cannot
    be removed is named on standard error and in the envelope's event stream,
    where a reader can see it beside the checks that ran in it.
    """
    problem = run.sandbox.discard(sandbox)
    if problem is None:
        return
    emit("mutation_preflight_sandbox_left", problem, stream=sys.stderr)
