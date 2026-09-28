"""Hold every pull-request lane to a selection the publisher measures.

A lane compiling a different selection from the baseline it ratchets
against compares a feature difference, not a commit difference, and a
number comes out either way. A publisher may measure more than one leg,
such as one per feature set or one per platform, so each lane generator
is paired with a publisher generator of the same selection rather than
with a single one, and each job ratchets exactly one of its legs: the
action keys the baseline by job, so a second ratchet in one job would
overwrite the first.
"""

from __future__ import annotations

import re
import typing as typ

from .publisher import (
    COVERAGE_ACTION,
    PINNED_COMMIT,
    UPLOAD_ACTION,
    action_steps,
    invokes,
    pin_of,
    upload_step,
)
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    from .loading import Document

#: Inputs that may differ between a pull-request lane and the publisher,
#: because they name, ship or save the report rather than select what runs.
#: `publish-baseline` decides when the ratchet saves; a lane may not set it
#: at all, which `pull_request_lane_violations` holds.
LANE_LOCAL_INPUTS: typ.Final[frozenset[str]] = frozenset(
    {
        "artefact-name-suffix",
        "publish-artefact",
        "publish-baseline",
    }
)

#: Environment keys that pin or place a tool some other step installs. The
#: generator hands its whole environment to cargo, uv and the tests, so any
#: other key may change what is measured and is compared. These name a
#: version, revision or checksum, an install or cache location, or a
#: network retry policy, none of which changes what the tests execute.
TOOL_PIN_KEY: typ.Final[re.Pattern[str]] = re.compile(
    r"[A-Z0-9_]+_(?:VERSION|REV|SHA256(?:_[A-Z0-9]+)?)"
)
TOOL_PLACEMENT_KEYS: typ.Final[frozenset[str]] = frozenset(
    {
        "CARGO_HTTP_MULTIPLEXING",
        "CARGO_NET_RETRY",
        "UV_CACHE_DIR",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
    }
)


def inputs_of(step: dict[str, object]) -> dict[str, object]:
    """Return a step's `with` mapping, empty when it declares none."""
    inputs = step.get("with") or {}
    return typ.cast("dict[str, object]", inputs) if isinstance(inputs, dict) else {}


def is_true(value: object) -> bool:
    """Return whether an action input reads as true."""
    return value is True or value == "true"


def is_false(value: object) -> bool:
    """Return whether an action input reads as false."""
    return value is False or value == "false"


def generator_env(document: Document, step: dict[str, object]) -> dict[object, object]:
    """Return the environment a step runs with: workflow, job and step merged.

    GitHub layers `env` from the workflow, then the job, then the step, each
    overriding the last, so a pin set at any level reaches the action and a
    comparison of the step's own `env` alone would miss it.

    Parameters
    ----------
    document : Document
        The workflow holding the step.
    step : dict[str, object]
        The step, by identity, within one of the document's jobs.

    Returns
    -------
    dict[object, object]
        The merged environment.

    Examples
    --------
    >>> step = {"env": {"B": "2"}}
    >>> generator_env({"env": {"A": "1"}, "jobs": {"j": {"steps": [step]}}}, step)
    {'A': '1', 'B': '2'}

    """
    held = next(
        (job for job in jobs(document).values() if any(s is step for s in steps(job))),
        {},
    )
    merged: dict[object, object] = {}
    for scope in (document, held, step):
        env = scope.get("env")
        if isinstance(env, dict):
            merged.update(env)
    return merged


def is_measured(key: object) -> bool:
    """Return whether an environment key can change what a run measures.

    Examples
    --------
    >>> [is_measured(k) for k in ("UV_PYTHON", "RUFF_VERSION", "UV_TOOL_DIR")]
    [True, False, False]

    """
    name = str(key)
    return name not in TOOL_PLACEMENT_KEYS and TOOL_PIN_KEY.fullmatch(name) is None


def selection(document: Document, step: dict[str, object]) -> dict[str, object]:
    """Return the inputs and environment that decide what a run measures.

    The merged `env` counts as well as the inputs: the action builds its
    venv with whatever interpreter the environment selects, and two
    interpreters count lines differently. `with-ratchet` is read as a
    boolean, so `'false'` and an omitted input select the same leg.

    Returns
    -------
    dict[str, object]
        The step's measuring inputs, under `with`, and the measuring part
        of its merged `env`.

    """
    inputs = {
        key: value
        for key, value in inputs_of(step).items()
        if key not in LANE_LOCAL_INPUTS
    }
    inputs["with-ratchet"] = is_true(inputs.get("with-ratchet"))
    env = {
        key: value
        for key, value in generator_env(document, step).items()
        if is_measured(key)
    }
    return {"with": inputs, "env": env}


def ratchet_violations(document: Document, label: str) -> list[str]:
    """Require each job measuring coverage to ratchet exactly one of its legs.

    Parameters
    ----------
    document : Document
        The workflow whose jobs are held.
    label : str
        How findings name the workflow, such as its file name.

    Returns
    -------
    list[str]
        One violation for each job whose coverage steps ratchet other than
        exactly once.

    """
    counts = {
        name: [
            is_true(inputs_of(step).get("with-ratchet"))
            for step in steps(job)
            if invokes(step, COVERAGE_ACTION)
        ]
        for name, job in jobs(document).items()
    }
    return [
        f"{label}: job {name} must set with-ratchet: 'true' on exactly one "
        f"generate-coverage step; found {sum(legs)}"
        for name, legs in counts.items()
        if legs and sum(legs) != 1
    ]


def publisher_lane_violations(
    publisher: Document, closure: dict[str, Document]
) -> list[str]:
    """Require the publisher to ratchet the selection every lane measures.

    The publisher's generators, its uploader and every pull-request
    generator share one commit pin, so the lanes measure with the same
    action that writes their baseline, and each lane generator's selection
    equals one of the publisher's.

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
    if not generators:
        return ["the publisher must generate coverage; found no generate-coverage step"]
    measured = [selection(publisher, step) for step in generators]
    lane_steps = _lane_steps(closure)
    pinned = [*generators, upload_step(publisher), *(step for *_, step in lane_steps)]
    pins = {pin_of(step) for step in pinned}
    return (
        ratchet_violations(publisher, "the publisher")
        + [
            f"{name}: coverage selection differs from the publisher's"
            for name, document, step in lane_steps
            if _unpaired(selection(document, step), measured)
        ]
        + _pin_violations(pins)
    )


def _lane_steps(
    closure: dict[str, Document],
) -> list[tuple[str, Document, dict[str, object]]]:
    """Return each pull-request coverage step with its workflow's name and document."""
    return [
        (name, document, step)
        for name, document in sorted(closure.items())
        for step in action_steps(document, COVERAGE_ACTION)
    ]


def _unpaired(lane: dict[str, object], measured: list[dict[str, object]]) -> bool:
    """Return whether a lane leg's selection matches no publisher leg."""
    return lane not in measured


def _pin_violations(pins: set[str]) -> list[str]:
    """Require one commit pin across every generator and the uploader."""
    if len(pins) == 1 and all(PINNED_COMMIT.match(pin) for pin in pins):
        return []
    message = (
        f"{COVERAGE_ACTION} and {UPLOAD_ACTION} must share one commit pin: "
        f"{sorted(pins)}"
    )
    return [message]
