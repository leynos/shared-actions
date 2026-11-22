"""Running each declared check from the boundary it declares.

A subprocess or nested-pytest fixture needs a working directory, an import root
and an environment. Declaring them here makes that contract explicit instead of
leaving it in a comment beside the fixture.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

from .constants import FAILED, SKIPPED, UNKNOWN
from .copy_set import copy_entry_problems, mutmut_copy_set
from .diagnostics import last_line, timeout_message
from .models import Check, Declaration, PhaseResult, Ran, phase_outcome
from .ports import Invocation, Run, project_environment
from .schema import path_problem

_NO_CHECKS: typ.Final[str] = "no checks declared"
_WHOLE_TREE: typ.Final[str] = "the unmodified checkout, which this lane copies whole"
_NO_SANDBOX: typ.Final[str] = (
    "the checks' sandbox could not be established, so a check that passes "
    "here would not have been shown to survive into the mutation run"
)


@dc.dataclass(frozen=True, slots=True)
class Boundary:
    """The tree a check runs in, and how its findings name that tree.

    Attributes
    ----------
    root : Path
        The directory a check's ``cwd`` is anchored at.
    where : str
        The evidence's ``ran_in`` value: the path or the phrase naming *root*.
    noun : str
        The word a finding uses for *root*, such as ``sandbox``.

    """

    root: Path
    where: str
    noun: str


def _check_cwd(
    run: Run, root: Path, check: Check, noun: str
) -> tuple[Path | None, str]:
    """Return the working directory *check* names, or why it is unusable."""
    problem = path_problem(check.cwd) if check.cwd != "." else None
    if problem is not None:
        return None, f"checks[{check.name!r}].cwd {problem}"
    located, resolution = run.files.locate(root, check.cwd)
    if located is None:
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} could not be resolved: "
            f"{resolution}"
        )
    if not located.inside_root:
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} resolves to "
            f"{located.resolved!r} outside the {noun}, so the check would run "
            f"elsewhere"
        )
    directory = Path(located.resolved)
    if not run.files.is_directory(directory):
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} is not a directory in the {noun}"
        )
    return directory, ""


def log_stem(index: int, name: str) -> str:
    """Return a log filename stem unique to one declared check.

    The name is sanitized for the filesystem, which maps distinct names such as
    ``a/b`` and ``a-b`` onto one stem. The check's position keeps them apart,
    so a later check never overwrites the evidence of an earlier failure.

    Parameters
    ----------
    index : int
        The check's position in the declaration.
    name : str
        The check's declared name.

    Returns
    -------
    str
        The position as two digits, then the sanitized name.

    """
    sanitized = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in name
    )
    return f"{index:02d}-{sanitized}"


def _verdict(check: Check, outcome: Ran) -> tuple[str | None, dict[str, object]]:
    """Return one check's finding and evidence, judged from its bounded result.

    Parameters
    ----------
    check : Check
        The declaration the result is judged against.
    outcome : Ran
        The command's bounded streams, exit status and timeout state.

    Returns
    -------
    tuple[str | None, dict[str, object]]
        The finding that fails the check, or ``None`` when it passed, and the
        evidence the envelope records either way.

    """
    evidence: dict[str, object] = {
        "exit_code": outcome.exit_code,
        "cwd": check.cwd,
        "timed_out": outcome.timed_out,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
    }
    if outcome.timed_out:
        finding = (
            f"check {check.name!r} {timeout_message(check.timeout_seconds)} "
            f"from {check.cwd!r}: {last_line(outcome)}"
        )
        return finding, evidence
    if outcome.exit_code != 0:
        finding = (
            f"check {check.name!r} failed with exit code {outcome.exit_code} "
            f"from {check.cwd!r}: {last_line(outcome)}"
        )
        return finding, evidence
    return None, evidence


@dc.dataclass(frozen=True, slots=True)
class Review:
    """One check's verdict, its evidence, and the result its logs come from.

    Attributes
    ----------
    finding : str | None
        Why the check failed, or ``None`` when it passed.
    evidence : object
        The envelope entry for this check: its recorded result, or the reason
        it never ran.
    outcome : Ran | None
        The bounded result to write logs from, or ``None`` when no command ran.

    """

    finding: str | None
    evidence: object
    outcome: Ran | None


def _review_check(boundary: Boundary, check: Check, run: Run) -> Review:
    """Return one check's verdict, and the result its logs are written from."""
    directory, problem = _check_cwd(run, boundary.root, check, boundary.noun)
    if directory is None:
        return Review(problem, problem, None)
    # The project's interpreter is selected first and the declaration's own
    # environment layered over it, so an explicit override keeps the last word.
    outcome, problem = run.runner(
        Invocation(
            check.command,
            cwd=directory,
            env={**project_environment(run), **dict(check.env)},
            timeout=check.timeout_seconds,
            ambient=run.ambient,
        )
    )
    if outcome is None:
        finding = f"check {check.name!r} could not run from {check.cwd!r}: {problem}"
        return Review(finding, problem, None)
    finding, evidence = _verdict(check, outcome)
    return Review(finding, evidence, outcome)


def _run_checks(run: Run, declaration: Declaration, boundary: Boundary) -> PhaseResult:
    """Run every declared check under *boundary*, recording where that was.

    The logs are written here, at the orchestration edge, rather than by the
    function that judges a result: a timed-out check keeps the streams it
    produced before it was stopped, and a log that cannot be written is a named
    finding that fails the check.
    """
    reviews = [_review_check(boundary, check, run) for check in declaration.checks]
    findings: list[str] = []
    # Keyed by position as well as name: two checks may share a name, and a
    # mapping by name alone would silently drop one of their evidence entries.
    results: list[dict[str, object]] = []
    pairs = zip(declaration.checks, reviews, strict=True)
    for index, (check, review) in enumerate(pairs):
        finding = review.finding
        if review.outcome is not None:
            log_problem = run.files.write_check_logs(
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
        results.append(
            {"index": index, "name": check.name, "evidence": review.evidence}
        )
    detail: dict[str, object] = {"ran_in": boundary.where, "results": results}
    return phase_outcome("checks", findings, [], detail)


def _checks_boundary(run: Run, declaration: Declaration) -> PhaseResult | Boundary:
    """Return where the declared checks run, or the phase that refuses them.

    Contract selection and the sandbox's lifecycle are kept here, apart from
    running the checks. On the mutmut lane the boundary is a fresh copy built
    from the selected release's contract; the cargo lane copies the whole tree,
    so the checkout itself is the representative sandbox there.
    """
    if declaration.lane != "mutmut":
        return Boundary(run.root, _WHOLE_TREE, "checkout")
    table, problem = run.files.read_mutmut_configuration(run.root)
    invalid = copy_entry_problems(table)
    if invalid:
        # An escaping copy entry would put the sandbox somewhere the checks
        # must not run, so it is refused before anything is materialized.
        return PhaseResult("checks", FAILED, invalid, {"reason": _NO_SANDBOX})
    contract, problems = mutmut_copy_set(run.mutmut_version, table, problem)
    if contract is None:
        return PhaseResult("checks", UNKNOWN, problems, {"reason": _NO_SANDBOX})
    sandbox, problems = run.files.materialize(run.root, contract)
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
        return _run_checks(run, declaration, boundary)
    finally:
        if boundary.root != run.root:
            run.files.discard(boundary.root)
