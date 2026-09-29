"""Hold every coverage generator to the one Python the repository measures on.

`generate-coverage` builds its venv with the first interpreter its resolver
finds: the `python-version` input, then `UV_PYTHON`, then `.python-version`,
then the `python3` on `PATH`, which `actions/setup-python` puts there. A
generator that leaves the choice to the runner measures under whatever Python
it holds, and two interpreters count lines differently. So each generator
must pin `UV_PYTHON` to the configured version, and no other source it
declares may name a different one, since a higher-priority value would then
silently override a lower one.
"""

from __future__ import annotations

import re
import typing as typ

from .parity import generator_env, inputs_of, python_key
from .publisher import COVERAGE_ACTION, action_steps, invokes, position
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    from .loading import Document

#: An explicit interpreter version, such as `3.13` or `3.13.5`. uv also
#: accepts `3` or `>=3.12`, but those still take the newest match on the
#: runner, which is the drift this pin exists to stop.
BOUNDED_PYTHON: typ.Final[re.Pattern[str]] = re.compile(r"\d+\.\d+(?:\.\d+)?")
SETUP_PYTHON: typ.Final[str] = "actions/setup-python"


def interpreter_violations(
    publisher: Document,
    interpreter: str,
    lanes: dict[str, Document] | None = None,
) -> list[str]:
    """Require every generator to pin the configured interpreter, and only it.

    The publisher's generators are judged, and so is each pull-request lane's
    when `lanes` is given: the parity rule compares a lane's `UV_PYTHON` with
    the publisher's, but says nothing about a `python-version` input or a
    `setup-python` step that disagrees with it inside the lane.

    Parameters
    ----------
    publisher : Document
        The publisher workflow document.
    interpreter : str
        The explicit version the repository measures under, such as `3.13`.
        `3.13` and `3.13.0` name the same version; `3.13.1` does not.
    lanes : dict[str, Document] | None, optional
        The pull-request-reachable workflows, by file name.

    Returns
    -------
    list[str]
        One violation for a configured interpreter that is not an explicit
        version, or for each generator whose `UV_PYTHON` is not exactly
        `interpreter`, or that declares another version through another
        source.

    Examples
    --------
    >>> interpreter_violations({"jobs": {}}, "3")
    ["the configured interpreter '3' is not an explicit version such as 3.13"]

    """
    if BOUNDED_PYTHON.fullmatch(interpreter) is None:
        message = (
            f"the configured interpreter {interpreter!r} is not an explicit "
            "version such as 3.13"
        )
        return [message]
    found = _publisher_violations(publisher, interpreter)
    for name, document in sorted((lanes or {}).items()):
        found += [
            f"{name}: {problem}"
            for step in action_steps(document, COVERAGE_ACTION)
            for problem in _step_violations(document, step, interpreter)
        ]
    return found


def _publisher_violations(publisher: Document, interpreter: str) -> list[str]:
    """Judge the publisher's generators, which must exist."""
    generators = action_steps(publisher, COVERAGE_ACTION)
    problems = [
        problem
        for step in generators
        for problem in _step_violations(publisher, step, interpreter)
    ]
    return [
        f"the publisher's {problem}"
        for problem in problems or ([] if generators else [_unpinned(interpreter)])
    ]


def _unpinned(interpreter: str) -> str:
    """Name the pin a generator lacks, and why it is required."""
    return (
        f"generate-coverage must set env UV_PYTHON: '{interpreter}'; the "
        "action's venv otherwise takes the newest Python on the runner"
    )


def _step_violations(
    document: Document, step: dict[str, object], interpreter: str
) -> list[str]:
    """Judge one generator: its pin first, then every other declared source."""
    if not _is_pinned(document, step, interpreter):
        return [_unpinned(interpreter) + _masking_note(document, step)]
    return [
        f"generate-coverage names Python {version!r} through {source} but "
        f"pins {interpreter!r} through UV_PYTHON"
        for source, version in _other_sources(document, step)
        if python_key(version) != python_key(interpreter)
    ]


def _is_pinned(document: Document, step: dict[str, object], interpreter: str) -> bool:
    """Return whether the step's merged `env` pins exactly `interpreter`."""
    match generator_env(document, step):
        case {"UV_PYTHON": str() as version}:
            return python_key(version) == python_key(interpreter)
        case _:
            return False


def _masking_note(document: Document, step: dict[str, object]) -> str:
    """Say when an empty step or job `UV_PYTHON` hides a pin set further out.

    GitHub applies the innermost `env` last, and the action reads an empty
    `UV_PYTHON` as unset, so an empty value shadows the pin the workflow or
    job declared and the resolver falls through to the next source.
    """
    outer = [document, _job_of(document, step)]
    set_outside = any(_declares_version(scope) for scope in outer)
    if _text(generator_env(document, step).get("UV_PYTHON")) == "" and set_outside:
        return "; an empty UV_PYTHON here masks the one set in an outer scope"
    return ""


def _declares_version(scope: Document | dict[str, object]) -> bool:
    """Return whether a scope's `env` sets `UV_PYTHON` to a non-empty string."""
    env = scope.get("env")
    return isinstance(env, dict) and bool(str(env.get("UV_PYTHON") or "").strip())


def _job_of(document: Document, step: dict[str, object]) -> dict[str, object]:
    """Return the job holding a step, by identity; empty if none does."""
    return next((job for job in jobs(document).values() if _holds(job, step)), {})


def _holds(job: dict[str, object], step: dict[str, object]) -> bool:
    """Return whether a job holds a step, by identity."""
    return any(other is step for other in steps(job))


def _other_sources(
    document: Document, step: dict[str, object]
) -> list[tuple[str, str]]:
    """Return the versions the input and `setup-python` declare for a step."""
    found: list[tuple[str, str]] = []
    if version := _text(inputs_of(step).get("python-version")):
        found.append(("the python-version input", version))
    found += [
        ("a setup-python step", version)
        for version in _path_versions(document, step)
        if version
    ]
    return found


def _text(value: object) -> str:
    """Return a value as the resolver reads it: stripped text, empty if unset."""
    return "" if value is None else str(value).strip()


def _path_versions(document: Document, step: dict[str, object]) -> list[str]:
    """Return the Pythons a generator's job may have on `PATH` from setup steps.

    Only setups earlier in the same job count. The last one that always runs
    is what `PATH` holds unless a later one replaces it, so a `setup-python`
    guarded by `if:` or allowed to fail green after it may put a different
    Python there, and its version counts as well. Guarded setups with no
    reliable one before them claim nothing: `PATH` would then hold whatever
    the runner has, which no version can disagree with.
    """
    held = _job_of(document, step)
    before = [
        candidate
        for candidate in steps(held)[: position(steps(held), step)]
        if invokes(candidate, SETUP_PYTHON)
    ]
    reliable = [index for index, setup in enumerate(before) if not _may_not_run(setup)]
    if not reliable:
        return []
    return [
        _text(inputs_of(setup).get("python-version"))
        for setup in before[reliable[-1] :]
    ]


def _may_not_run(setup: dict[str, object]) -> bool:
    """Return whether a step may be skipped, or may fail without failing the job."""
    return "if" in setup or setup.get("continue-on-error", False) is not False
