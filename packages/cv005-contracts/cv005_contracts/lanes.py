"""Rules for the coverage lanes: pull requests ratchet, main writes the baseline.

generate-coverage saves its ratchet baseline on a push to main, so the
publisher must be the only lane that measures on that event, and every
pull-request lane must measure what the publisher measures: a lane
compiling a different selection compares a feature difference, not a
commit difference, and a number comes out either way.
"""

from __future__ import annotations

import re
import typing as typ

from .closure import reachable
from .expressions import ConditionError, missing_terms
from .publisher import (
    COVERAGE_ACTION,
    PINNED_COMMIT,
    UPLOAD_ACTION,
    action_steps,
    invokes,
    pin_of,
    upload_step,
)
from .reading import jobs, steps, triggers

if typ.TYPE_CHECKING:
    from .loading import Document

#: Inputs that may differ between a pull-request lane and the publisher,
#: because they name or ship the report rather than select what runs.
LANE_LOCAL_INPUTS: typ.Final[frozenset[str]] = frozenset(
    {
        "artefact-name-suffix",
        "publish-artefact",
    }
)
PULL_REQUEST_GUARD: typ.Final[frozenset[str]] = frozenset(
    {
        "github.event_name == 'pull_request'",
    }
)


def _inputs(step: dict[str, object]) -> dict[str, object]:
    """Return a step's `with` mapping, empty when it declares none."""
    inputs = step.get("with") or {}
    return typ.cast("dict[str, object]", inputs) if isinstance(inputs, dict) else {}


def _is_true(value: object) -> bool:
    """Return whether an action input reads as true."""
    return value is True or value == "true"


def _is_false(value: object) -> bool:
    """Return whether an action input reads as false."""
    return value is False or value == "false"


def pull_request_lane_violations(closure: dict[str, Document]) -> list[str]:
    """Require every pull-request lane to ratchet and publish nothing.

    Parameters
    ----------
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.

    Returns
    -------
    list[str]
        Every violation of the pull-request lanes' ratchet-only shape.

    """
    lanes = [
        (name, step)
        for name, document in sorted(closure.items())
        for step in action_steps(document, COVERAGE_ACTION)
    ]
    if not lanes:
        return ["no pull-request workflow generates coverage"]
    return [
        f"{name}: generate-coverage must set {key}: {wanted!r}"
        for name, step in lanes
        for key, wanted, check in (
            ("with-ratchet", "true", _is_true),
            ("publish-artefact", "false", _is_false),
        )
        if not check(_inputs(step).get(key))
    ]


def _guarded_to_pull_requests(holder: dict[str, object]) -> bool:
    """Return whether a step or a job runs only for a pull request."""
    try:
        return not missing_terms(holder.get("if"), PULL_REQUEST_GUARD)
    except ConditionError:
        return False


def _push_coverage_steps(document: Document) -> list[dict[str, object]]:
    """Return the coverage steps that can run when a push starts the document.

    A job guarded to pull requests never runs on a push, and neither does
    any step in it, whatever the step's own condition says.

    Returns
    -------
    list[dict[str, object]]
        The coverage steps a push can run.

    """
    return [
        step
        for job in jobs(document).values()
        if not _guarded_to_pull_requests(job)
        for step in steps(job)
        if invokes(step, COVERAGE_ACTION) and not _guarded_to_pull_requests(step)
    ]


def second_writer_violations(
    documents: dict[str, Document], publisher: str, repository: str
) -> list[str]:
    """Refuse coverage on a push anywhere but the publisher.

    A lane running on both events would write a second baseline on every
    push to main, so each generate-coverage step that another push-started
    workflow reaches, itself or through a local reusable workflow it
    calls, must run for pull requests only. A guard on the step or on its
    job counts, and a call made from a job guarded to pull requests is not
    followed, since that job never runs on a push.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    publisher : str
        The publisher's file name.
    repository : str
        The owner and name of the repository the workflows belong to.

    Returns
    -------
    list[str]
        Every violation of the second-writer rule.

    """
    seeds = [
        name
        for name, document in documents.items()
        if name != publisher and "push" in triggers(document)
    ]
    closure = reachable(documents, seeds, repository, _guarded_to_pull_requests)
    return [
        f"{name}: generate-coverage can run on a push; guard it to pull requests"
        for name, document in sorted(closure.items())
        if name != publisher
        for _ in _push_coverage_steps(document)
    ]


def _selection(step: dict[str, object]) -> dict[str, object]:
    """Return the inputs and environment that decide what a run measures.

    The step's `env` counts as well as its inputs: the action builds its
    venv with whatever interpreter the environment selects, and two
    interpreters count lines differently.

    Returns
    -------
    dict[str, object]
        The step's measuring inputs, under `with`, and its `env`.

    """
    inputs = {
        key: value
        for key, value in _inputs(step).items()
        if key not in LANE_LOCAL_INPUTS
    }
    return {"with": inputs, "env": step.get("env")}


#: An explicit interpreter version, such as `3.13` or `3.13.5`. uv also
#: accepts `3` or `>=3.12`, but those still take the newest match on the
#: runner, which is the drift this pin exists to stop.
_BOUNDED_PYTHON: typ.Final[re.Pattern[str]] = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _pins_interpreter(step: dict[str, object], interpreter: str) -> bool:
    """Return whether a coverage step's `env` pins exactly `interpreter`."""
    match step.get("env"):
        case {"UV_PYTHON": str() as version}:
            return version == interpreter
        case _:
            return False


def interpreter_violations(publisher: Document, interpreter: str) -> list[str]:
    """Require the publisher's generator to pin the configured interpreter.

    `generate-coverage` builds its venv with `uv venv`, which takes the
    newest Python it can find, so a lane whose earlier steps download
    another Python measures under that one. The lanes rule already holds
    every generator's `env` equal to the publisher's, so pinning the
    publisher pins them all.

    Parameters
    ----------
    publisher : Document
        The publisher workflow document.
    interpreter : str
        The explicit version the repository measures under, such as `3.13`.

    Returns
    -------
    list[str]
        One violation when the generator's `UV_PYTHON` is not exactly
        `interpreter`, or when `interpreter` itself is not an explicit
        version.

    Examples
    --------
    >>> interpreter_violations({"jobs": {}}, "3")
    ["the configured interpreter '3' is not an explicit version such as 3.13"]

    """
    if _BOUNDED_PYTHON.fullmatch(interpreter) is None:
        message = (
            f"the configured interpreter {interpreter!r} is not an explicit "
            "version such as 3.13"
        )
        return [message]
    generators = action_steps(publisher, COVERAGE_ACTION)
    if generators and all(_pins_interpreter(step, interpreter) for step in generators):
        return []
    message = (
        f"the publisher's generate-coverage must set env UV_PYTHON: "
        f"'{interpreter}'; the action's venv otherwise takes the newest Python "
        "on the runner"
    )
    return [message]


def publisher_lane_violations(
    publisher: Document, closure: dict[str, Document]
) -> list[str]:
    """Require the publisher to ratchet the selection every lane measures.

    The publisher's generator, its uploader and every pull-request
    generator share one commit pin, so the lanes measure with the same
    action that writes their baseline. Each generator also carries the same
    `env`: a baseline measured under one Python and a lane measured under
    another differ by the lines each counts, not by the tests.
    `interpreter_violations` holds the pin itself where a repository measures
    Python.

    Parameters
    ----------
    publisher : Document
        The publisher workflow document.
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.

    Returns
    -------
    list[str]
        Every violation of the shared-selection and shared-pin rules.

    """
    generators = action_steps(publisher, COVERAGE_ACTION)
    if len(generators) != 1:
        return [f"the publisher must generate coverage once; found {len(generators)}"]
    baseline = generators[0]
    found = (
        []
        if _is_true(_inputs(baseline).get("with-ratchet"))
        else ["the publisher's generate-coverage must set with-ratchet: 'true'"]
    )
    pins = {pin_of(baseline), pin_of(upload_step(publisher))}
    for name, document in sorted(closure.items()):
        for step in action_steps(document, COVERAGE_ACTION):
            pins.add(pin_of(step))
            if _selection(step) != _selection(baseline):
                found.append(f"{name}: coverage selection differs from the publisher's")
    if len(pins) != 1 or not all(PINNED_COMMIT.match(pin) for pin in pins):
        found.append(
            f"{COVERAGE_ACTION} and {UPLOAD_ACTION} must share one commit pin: "
            f"{sorted(pins)}"
        )
    return found
