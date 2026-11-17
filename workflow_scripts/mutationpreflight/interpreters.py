"""Judging the project interpreter, and reporting both runtimes separately.

The rule itself is not restated here. It is the closed cv005-contracts rule
(#565), loaded by path from this checkout, so a bare ``X.Y`` is decided in one
place rather than twice.
"""

from __future__ import annotations

import importlib.util
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from packaging.version import InvalidVersion, Version

from .constants import HELPER_PYTHON_FLOOR, SHARED_RULE
from .models import PhaseResult, Runtimes, phase_outcome
from .toml_documents import read_document

_NO_PROJECT_PYTHON: typ.Final[str] = (
    "no project interpreter was supplied, so requires-python was not checked; "
    "set the workflow's python-version input"
)


def _load_shared_rule() -> tuple[typ.Callable[..., list[str]] | None, str]:
    """Return the shared interpreter rule (#565), or why it is unavailable.

    It is loaded by path rather than imported as a package: the caller's
    environment holds the caller's dependencies, not this repository's, and
    importing ``cv005_contracts`` would pull its whole contract graph in for
    one pure function.
    """
    if not SHARED_RULE.is_file():
        return None, f"the shared requires-python rule is absent: {SHARED_RULE}"
    spec = importlib.util.spec_from_file_location(
        "_mutation_preflight_requires_python", SHARED_RULE
    )
    if spec is None or spec.loader is None:
        absent = f"the shared requires-python rule could not be loaded: {SHARED_RULE}"
        return None, absent
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        return None, f"the shared requires-python rule failed to load: {error}"
    rule = getattr(module, "requirement_violations", None)
    if rule is None:
        return None, (
            f"the shared rule at {SHARED_RULE} no longer exposes requirement_violations"
        )
    return rule, ""


def judge(requirement: object, interpreter: str, *, subject: str) -> list[str]:
    """Return why *interpreter* falls outside *requirement*, using #565's rule.

    Parameters
    ----------
    requirement : object
        The PEP 508 range to judge.
    interpreter : str
        The interpreter to judge, such as ``3.13`` or ``3.13.5``.
    subject : str
        What the range belongs to, used in the finding.

    Returns
    -------
    list[str]
        One finding per violation, or the reason the shared rule could not be
        loaded.

    """
    rule, problem = _load_shared_rule()
    if rule is None:
        return [problem]
    return list(rule(requirement, interpreter, subject=subject))


def _project_requirement(repo_root: Path) -> tuple[object, str | None]:
    """Return the project's declared ``requires-python``, or why it went unread.

    A read failure is returned separately from the range itself: both would
    otherwise be a ``str``, and a failure is a reason to report unknown support
    rather than a requirement to judge.
    """
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return None, None
    document, problem = read_document(path)
    if problem is not None or document is None:
        return None, problem or f"{path} could not be read"
    project = document.get("project")
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
    requirement: object, interpreter: str, blocking: list[str], unknown: list[str]
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
    blocking.extend(
        judge(requirement, interpreter, subject="the project's requires-python")
    )


def run_interpreter_phase(repo_root: Path, runtimes: Runtimes) -> PhaseResult:
    """Judge the project interpreter, reporting both runtimes separately.

    Parameters
    ----------
    repo_root : Path
        The checkout whose ``requires-python`` is judged.
    runtimes : Runtimes
        The helper and project interpreters, kept apart.

    Returns
    -------
    PhaseResult
        ``pass`` when the project range holds, ``fail`` when it does not,
        ``unknown`` when no project interpreter was supplied or the range
        could not be read.

    """
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
    requirement, problem = _project_requirement(repo_root)
    detail["requires_python"] = requirement
    if problem is not None:
        indeterminate.append(problem)
    _judge_project_range(requirement, runtimes.project, blocking, indeterminate)
    return phase_outcome("interpreter", blocking, indeterminate, detail)
