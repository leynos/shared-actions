"""Running each declared check from the boundary it declares.

A subprocess or nested-pytest fixture needs a working directory, an import root
and an environment. Declaring them here makes that contract explicit instead of
leaving it in a comment beside the fixture.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

from .diagnostics import last_line, timeout_message
from .ports import Invocation, Run, project_environment
from .schema import path_problem

if typ.TYPE_CHECKING:
    from .models import Check, Declaration, Ran


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
    owned : bool
        Whether the run created *root* and must remove it, rather than the
        checkout it only reads.

    """

    root: Path
    where: str
    noun: str
    owned: bool = False


def _check_cwd(
    run: Run, root: Path, check: Check, noun: str
) -> tuple[Path | None, str]:
    """Return the working directory *check* names, or why it is unusable."""
    problem = path_problem(check.cwd) if check.cwd != "." else None
    if problem is not None:
        return None, f"checks[{check.name!r}].cwd {problem}"
    located, resolution = run.reader.locate(root, check.cwd)
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
    if not run.reader.is_directory(directory):
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
            resolver=run.resolver,
        )
    )
    if outcome is None:
        finding = f"check {check.name!r} could not run from {check.cwd!r}: {problem}"
        return Review(finding, problem, None)
    finding, evidence = _verdict(check, outcome)
    return Review(finding, evidence, outcome)


def run_checks(
    run: Run, declaration: Declaration, boundary: Boundary
) -> tuple[list[Review], list[dict[str, object]]]:
    """Run every declared check under *boundary*, returning what each decided.

    The logs are written by the caller from the reviews returned here, so this
    function runs and judges the checks and nothing else. A timed-out check
    still returns the streams it produced before it was stopped, so a log can
    carry them.
    """
    reviews = [_review_check(boundary, check, run) for check in declaration.checks]
    results = [
        {"index": index, "name": check.name, "evidence": review.evidence}
        for index, (check, review) in enumerate(
            zip(declaration.checks, reviews, strict=True)
        )
    ]
    return reviews, results
