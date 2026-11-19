"""Running each declared check from the boundary it declares.

A subprocess or nested-pytest fixture needs a working directory, an import root
and an environment. Declaring them here makes that contract explicit instead of
leaving it in a comment beside the fixture.
"""

from __future__ import annotations

import dataclasses as dc
import sys
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .console import emit
from .constants import FAILED, SKIPPED, UNKNOWN
from .copy_set import mutmut_copy_set
from .materialize import discard, materialize
from .models import Check, Declaration, PhaseResult, Ran, phase_outcome
from .running import last_line, run_command
from .sandbox import path_problem

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


def _check_cwd(root: Path, check: Check, noun: str) -> tuple[Path | None, str]:
    """Return the working directory *check* names, or why it is unusable."""
    problem = path_problem(check.cwd) if check.cwd != "." else None
    if problem is not None:
        return None, f"checks[{check.name!r}].cwd {problem}"
    anchor = root.resolve()
    directory = (anchor / check.cwd).resolve()
    if not directory.is_relative_to(anchor):
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} resolves to {str(directory)!r} "
            f"outside the {noun}, so the check would run elsewhere"
        )
    if not directory.is_dir():
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


def write_logs(directory: Path, stem: str, name: str, outcome: Ran) -> None:
    """Write one check's bounded streams, so a failure keeps its evidence.

    Parameters
    ----------
    directory : Path
        The ``checks/`` directory the logs are written into.
    stem : str
        The filename stem that keeps this check's logs apart.
    name : str
        The check's declared name, used when a log error is reported.
    outcome : Ran
        The bounded streams to write as ``<stem>.stdout.log`` and
        ``<stem>.stderr.log``.

    An ``OSError`` while writing is reported as a
    ``mutation_preflight_log_error`` line rather than raised, so one unwritable
    log does not discard the phase's verdict.

    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}.stdout.log").write_text(outcome.stdout, encoding="utf-8")
        (directory / f"{stem}.stderr.log").write_text(outcome.stderr, encoding="utf-8")
    except OSError as error:
        emit(
            "mutation_preflight_log_error",
            f"could not write logs for check {name!r}: {error}",
            stream=sys.stderr,
        )


def _review_check(
    boundary: Boundary, check: Check, output_dir: Path, index: int
) -> tuple[str | None, object]:
    """Return one check's finding, and the evidence it left behind."""
    directory, problem = _check_cwd(boundary.root, check, boundary.noun)
    if directory is None:
        return problem, problem
    outcome, problem = run_command(
        check.command,
        cwd=directory,
        env=dict(check.env),
        timeout=check.timeout_seconds,
    )
    if outcome is None:
        finding = f"check {check.name!r} could not run from {check.cwd!r}: {problem}"
        return finding, problem
    write_logs(output_dir / "checks", log_stem(index, check.name), check.name, outcome)
    evidence: dict[str, object] = {
        "exit_code": outcome.exit_code,
        "cwd": check.cwd,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
    }
    if outcome.exit_code != 0:
        finding = (
            f"check {check.name!r} failed with exit code {outcome.exit_code} "
            f"from {check.cwd!r}: {last_line(outcome)}"
        )
        return finding, evidence
    return None, evidence


def _run_checks(
    boundary: Boundary, declaration: Declaration, output_dir: Path
) -> PhaseResult:
    """Run every declared check under *boundary*, recording where that was."""
    pairs = [
        (check, _review_check(boundary, check, output_dir, index))
        for index, check in enumerate(declaration.checks)
    ]
    findings = [finding for _, (finding, _) in pairs if finding is not None]
    detail: dict[str, object] = {check.name: evidence for check, (_, evidence) in pairs}
    detail["ran_in"] = boundary.where
    return phase_outcome("checks", findings, [], detail)


def run_checks_phase(
    repo_root: Path, declaration: Declaration, output_dir: Path, mutmut_version: str
) -> PhaseResult:
    """Run each declared check from the boundary it declares.

    On the mutmut lane the checks run inside a copy built from that release's
    contract, because a check that only passes in the checkout has not been
    shown to survive into ``mutants/``. The cargo lane copies the whole tree,
    so the checkout itself is the representative sandbox there.

    Parameters
    ----------
    repo_root : Path
        The checkout the declaration belongs to.
    declaration : Declaration
        The repository's declaration, whose ``checks`` are run.
    output_dir : Path
        Where each check's bounded streams are written.
    mutmut_version : str
        The mutmut release whose copy contract the sandbox is built from.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the sandbox
        cannot be established, ``fail`` when a check cannot run or exits
        non-zero, ``pass`` otherwise.

    """
    if not declaration.checks:
        return PhaseResult("checks", SKIPPED, (), {"reason": _NO_CHECKS})
    if declaration.lane != "mutmut":
        return _run_checks(
            Boundary(repo_root, _WHOLE_TREE, "checkout"), declaration, output_dir
        )
    contract, problems = mutmut_copy_set(repo_root, mutmut_version)
    if contract is None:
        return PhaseResult("checks", UNKNOWN, problems, {"reason": _NO_SANDBOX})
    sandbox, problems = materialize(repo_root, contract)
    if sandbox is None:
        return PhaseResult("checks", FAILED, list(problems), {"reason": _NO_SANDBOX})
    try:
        boundary = Boundary(sandbox, str(sandbox), "sandbox")
        return _run_checks(boundary, declaration, output_dir)
    finally:
        discard(sandbox)
