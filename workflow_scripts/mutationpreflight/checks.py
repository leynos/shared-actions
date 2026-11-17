"""Running each declared check from the boundary it declares.

A subprocess or nested-pytest fixture needs a working directory, an import root
and an environment. Declaring them here makes that contract explicit instead of
leaving it in a comment beside the fixture.
"""

from __future__ import annotations

import sys
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .console import emit
from .constants import SKIPPED
from .models import Check, Declaration, PhaseResult, Ran, phase_outcome
from .running import last_line, run_command
from .sandbox import path_problem

_NO_CHECKS: typ.Final[str] = "no checks declared"


def _check_cwd(repo_root: Path, check: Check) -> tuple[Path | None, str]:
    """Return the working directory *check* names, or why it is unusable."""
    problem = path_problem(check.cwd) if check.cwd != "." else None
    if problem is not None:
        return None, f"checks[{check.name!r}].cwd {problem}"
    root = repo_root.resolve()
    directory = (root / check.cwd).resolve()
    if not directory.is_relative_to(root):
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} resolves to {str(directory)!r} "
            f"outside the checkout, so the check would run elsewhere"
        )
    if not directory.is_dir():
        return None, (
            f"checks[{check.name!r}].cwd {check.cwd!r} is not a directory in "
            f"the checkout"
        )
    return directory, ""


def log_stem(index: int, name: str) -> str:
    """Return a log filename stem unique to one declared check.

    The name is sanitised for the filesystem, which maps distinct names such as
    ``a/b`` and ``a-b`` onto one stem. The check's position keeps them apart,
    so a later check never overwrites the evidence of an earlier failure.
    """
    sanitised = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in name
    )
    return f"{index:02d}-{sanitised}"


def write_logs(directory: Path, stem: str, name: str, outcome: Ran) -> None:
    """Write one check's bounded streams, so a failure keeps its evidence."""
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
    repo_root: Path, check: Check, output_dir: Path, index: int
) -> tuple[str | None, object]:
    """Return one check's finding, and the evidence it left behind."""
    directory, problem = _check_cwd(repo_root, check)
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


def run_checks_phase(
    repo_root: Path, declaration: Declaration, output_dir: Path
) -> PhaseResult:
    """Run each declared check from the boundary it declares.

    Parameters
    ----------
    repo_root : Path
        The checkout the declared working directories are resolved against.
    declaration : Declaration
        The repository's declaration, whose ``checks`` are run.
    output_dir : Path
        Where each check's bounded streams are written.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``fail`` when a check cannot run
        or exits non-zero, ``pass`` otherwise.

    """
    if not declaration.checks:
        return PhaseResult("checks", SKIPPED, (), {"reason": _NO_CHECKS})
    pairs = [
        (check, _review_check(repo_root, check, output_dir, index))
        for index, check in enumerate(declaration.checks)
    ]
    findings = [finding for _, (finding, _) in pairs if finding is not None]
    detail = {check.name: evidence for check, (_, evidence) in pairs}
    return phase_outcome("checks", findings, [], detail)
