"""Judging the project interpreter, and reporting both runtimes separately.

The rule itself is not restated here. It is the closed cv005-contracts rule
(#565), loaded from this checkout by the repository adapter, so a bare ``X.Y``
is decided in one place rather than twice.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .constants import HELPER_PYTHON_FLOOR
from .models import PhaseResult, phase_outcome

if typ.TYPE_CHECKING:
    from .ports import RequirementRule, Run

_NO_PROJECT_PYTHON: typ.Final[str] = (
    "no project interpreter was supplied, so requires-python was not checked; "
    "set the workflow's python-version input"
)


def judge(
    rule: RequirementRule | None,
    rule_problem: str,
    requirement: object,
    interpreter: str,
    *,
    subject: str,
) -> tuple[list[str], list[str]]:
    """Return why *interpreter* falls outside *requirement*, using #565's rule.

    The rule and the reason it is absent are passed explicitly rather than as
    the whole run, so this function depends only on what it reads.

    Parameters
    ----------
    rule : RequirementRule | None
        The shared rule that decides the range.
    rule_problem : str
        Why *rule* is ``None``, empty when it is loaded.
    requirement : object
        The PEP 508 range to judge.
    interpreter : str
        The interpreter to judge, such as ``3.13`` or ``3.13.5``.
    subject : str
        What the range belongs to, used in the finding.

    Returns
    -------
    tuple[list[str], list[str]]
        One finding per violation, and separately the findings that leave
        support unknown. The two are distinct: a rule that will not load judged
        nothing, so it leaves support unknown rather than failing the
        interpreter it never read.

    """
    if rule is None:
        return [], [rule_problem]
    return list(rule(requirement, interpreter, subject=subject)), []


def _project_requirement(run: Run) -> tuple[object, str | None]:
    """Return the project's declared ``requires-python``, or why it went unread.

    A read failure is returned separately from the range itself: both would
    otherwise be a ``str``, and a failure is a reason to report unknown support
    rather than a requirement to judge.
    """
    documents = run.reader.read_documents(run.root, (Path("pyproject.toml"),))
    if not documents:
        return None, None
    document = documents[0]
    if document.problem:
        return None, document.problem
    project = (document.document or {}).get("project")
    if not isinstance(project, dict):
        return None, None
    return typ.cast("dict[str, object]", project).get("requires-python"), None


def _helper_floor_violations(helper_python: str) -> list[str]:
    """Return a finding when the helper's own runtime is below its floor."""
    try:
        running = Version(helper_python)
        floor = Version(HELPER_PYTHON_FLOOR)
    except InvalidVersion as error:
        return [f"the helper interpreter {helper_python!r} is not a version: {error}"]
    if running < floor:
        message = (
            f"the helper interpreter {helper_python!r} is below the helper "
            f"floor {HELPER_PYTHON_FLOOR}; the workflow's helper scripts need it"
        )
        return [message]
    return []


def _judge_project_range(
    run: Run, requirement: object, blocking: list[str], unknown: list[str]
) -> None:
    """Extend *blocking* or *unknown* from the project's declared range."""
    if requirement is None:
        return
    if not isinstance(requirement, str):
        unknown.append(
            f"the project's requires-python is not a range: {requirement!r}, so "
            f"interpreter compatibility is unknown rather than established"
        )
        return
    violations, indeterminate = judge(
        run.rule,
        run.rule_problem,
        requirement,
        run.runtimes.project,
        subject="the project's requires-python",
    )
    blocking.extend(violations)
    unknown.extend(indeterminate)


def run_interpreter_phase(run: Run) -> PhaseResult:
    """Judge the project interpreter, reporting both runtimes separately.

    Parameters
    ----------
    run : Run
        The checkout whose ``requires-python`` is judged, both interpreters,
        and the shared rule that decides the range.

    Returns
    -------
    PhaseResult
        ``pass`` when the project range holds, ``fail`` when it does not,
        ``unknown`` when no project interpreter was supplied or the range
        could not be read.

    """
    runtimes = run.runtimes
    detail: dict[str, object] = {
        "helper_python": runtimes.helper,
        "project_python": runtimes.project,
        "same_runtime": runtimes.helper == runtimes.project,
    }
    blocking = _helper_floor_violations(runtimes.helper)
    indeterminate: list[str] = []
    if not runtimes.project:
        indeterminate.append(_NO_PROJECT_PYTHON)
        return phase_outcome("interpreter", blocking, indeterminate, detail)
    requirement, problem = _project_requirement(run)
    # TOML may hand back a non-string for a misdeclared value (an unquoted date,
    # for instance), and the report is JSON: keep the evidence serializable.
    detail["requires_python"] = None if requirement is None else str(requirement)
    if problem is not None:
        indeterminate.append(problem)
    _judge_project_range(run, requirement, blocking, indeterminate)
    return phase_outcome("interpreter", blocking, indeterminate, detail)
