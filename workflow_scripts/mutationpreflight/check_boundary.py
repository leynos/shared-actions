"""Choosing the tree a declared check runs in, and owning its lifecycle.

The cargo lane copies the whole working tree, so its checks run in the
checkout. The mutmut lane copies a filtered set, so its checks run in a fresh
copy built from the selected release's contract — the same files the mutation
run will receive. Selecting that boundary, and removing it again, is kept apart
from running the checks and writing their logs.
"""

from __future__ import annotations

import typing as typ

from .checks import Boundary, Review, log_stem, run_checks
from .constants import FAILED, SKIPPED, UNKNOWN
from .copy_set import copy_entry_problems, mutmut_copy_set
from .models import Declaration, PhaseResult, phase_outcome

if typ.TYPE_CHECKING:
    from .ports import Run

_NO_CHECKS: typ.Final[str] = "no checks declared"
_WHOLE_TREE: typ.Final[str] = "the unmodified checkout, which this lane copies whole"
_NO_SANDBOX: typ.Final[str] = (
    "the checks' sandbox could not be established, so a check that passes "
    "here would not have been shown to survive into the mutation run"
)


def _checks_boundary(run: Run, declaration: Declaration) -> PhaseResult | Boundary:
    """Return where the declared checks run, or the phase that refuses them.

    Contract selection and the sandbox's lifecycle are kept here, apart from
    running the checks. On the mutmut lane the boundary is a fresh copy built
    from the selected release's contract; the cargo lane copies the whole tree,
    so the checkout itself is the representative sandbox there.
    """
    if declaration.lane != "mutmut":
        return Boundary(run.root, _WHOLE_TREE, "checkout")
    table, problem = run.reader.read_mutmut_configuration(run.root)
    invalid = copy_entry_problems(table)
    if invalid:
        # An escaping copy entry would put the sandbox somewhere the checks
        # must not run, so it is refused before anything is materialized.
        return PhaseResult("checks", FAILED, invalid, {"reason": _NO_SANDBOX})
    contract, problems = mutmut_copy_set(run.mutmut_version, table, problem)
    if contract is None:
        return PhaseResult("checks", UNKNOWN, problems, {"reason": _NO_SANDBOX})
    sandbox, problems = run.sandbox.materialize(run.root, contract)
    if sandbox is None:
        return PhaseResult("checks", FAILED, problems, {"reason": _NO_SANDBOX})
    return Boundary(sandbox, str(sandbox), "sandbox")


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
    boundary = _checks_boundary(run, declaration)
    if isinstance(boundary, PhaseResult):
        return boundary
    try:
        reviews, results = run_checks(run, declaration, boundary)
        findings = _write_logs(run, declaration, reviews)
        detail: dict[str, object] = {"ran_in": boundary.where, "results": results}
        return phase_outcome("checks", findings, [], detail)
    finally:
        if boundary.root != run.root:
            run.sandbox.discard(boundary.root)


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
