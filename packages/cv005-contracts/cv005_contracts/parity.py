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

from .legs import Leg, generator_legs, leg_id
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
    import collections.abc as cabc

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


def python_key(version: str) -> str:
    """Return a Python version in one spelling, so `3.13` and `3.13.0` agree.

    A trailing `.0` patch names the same version as the bare minor under
    PEP 440, and workflows spell it both ways. Nothing else is folded: `3.13.1`
    is another version, and so is `3.130`.

    Examples
    --------
    >>> [python_key(v) for v in ("3.13", "3.13.0", " 3.13.0 ", "3.13.1", "3.130")]
    ['3.13', '3.13', '3.13', '3.13.1', '3.130']

    """
    parts = version.strip().split(".")
    return ".".join(parts[:2] if len(parts) == 3 and parts[2] == "0" else parts)


#: Keys that match the tool-pin pattern but that `generate-coverage` itself
#: reads, to choose the version of a tool it installs to measure with. Two
#: legs differing in one would measure with different tools, so such a key is
#: compared. The action reads none today: its installer versions are constants
#: in its scripts. `tests/workflows/test_cv005_parity_exclusions.py` holds this
#: set equal to what the action reads, so a new read cannot slip under a
#: pattern, and adding the key here is the fix that test asks for.
ACTION_READ_KEYS: typ.Final[frozenset[str]] = frozenset()


def matches_exclusion(key: object) -> bool:
    """Return whether a key matches a pattern of the handwritten exclusion.

    Examples
    --------
    >>> [matches_exclusion(k) for k in ("UV_PYTHON", "RUFF_VERSION", "UV_TOOL_DIR")]
    [False, True, True]

    """
    name = str(key)
    return name in TOOL_PLACEMENT_KEYS or TOOL_PIN_KEY.fullmatch(name) is not None


def is_measured(key: object) -> bool:
    """Return whether an environment key can change what a run measures.

    A key the exclusion's patterns match is still measured when the action
    reads it.

    Examples
    --------
    >>> [is_measured(k) for k in ("UV_PYTHON", "RUFF_VERSION", "UV_TOOL_DIR")]
    [True, False, False]

    """
    return str(key) in ACTION_READ_KEYS or not matches_exclusion(key)


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
    _fold_python_spellings(inputs, env)
    return {"with": inputs, "env": env}


def _fold_python_spellings(*mappings: dict[typ.Any, object]) -> None:
    """Rewrite each interpreter request in one spelling, in place.

    Only the two keys the resolver reads are folded, and only when they hold
    text; any other value is left as written, so it still differs.
    """
    for held in mappings:
        for key in ("python-version", "UV_PYTHON"):
            if isinstance(held.get(key), str):
                held[key] = python_key(str(held[key]))


def ratchet_violations(
    document: Document,
    label: str,
    alternatives: cabc.Mapping[str, frozenset[str]] | None = None,
) -> list[str]:
    """Require each job measuring coverage to ratchet exactly one of its legs.

    Parameters
    ----------
    document : Document
        The workflow whose jobs are held.
    label : str
        How findings name the workflow, such as its file name; it also begins
        each leg's name.
    alternatives : Mapping[str, frozenset[str]] | None, optional
        Declared lane legs that stand in for one another in a matrix job, by
        leg name, each with the extra conditions that select it. Where every
        such leg of a job carries a different, non-empty set, only one runs in
        any cell, so together they ratchet once.

    Returns
    -------
    list[str]
        One violation for each job whose coverage steps ratchet other than
        exactly once.

    """
    counts = {
        name: _ratchets(
            [
                leg_id(label, name, step, index)
                for index, step in enumerate(steps(job))
                if invokes(step, COVERAGE_ACTION)
                and is_true(inputs_of(step).get("with-ratchet"))
            ],
            alternatives or {},
        )
        for name, job in jobs(document).items()
        if any(invokes(step, COVERAGE_ACTION) for step in steps(job))
    }
    return [
        f"{label}: job {name} must set with-ratchet: 'true' on exactly one "
        f"generate-coverage step; found {count}"
        for name, count in counts.items()
        if count != 1
    ]


def _ratchets(
    ratcheting: list[str], alternatives: cabc.Mapping[str, frozenset[str]]
) -> int:
    """Count a job's ratchets, taking declared alternatives as one."""
    paired = [ident for ident in ratcheting if ident in alternatives]
    selectors = [alternatives[ident] for ident in paired]
    is_exclusive = all(selectors) and len(set(selectors)) == len(selectors)
    alone = len(ratcheting) - len(paired)
    return alone + (1 if paired and is_exclusive else len(paired))


def publisher_lane_violations(
    publisher: Document,
    closure: dict[str, Document],
    declared: cabc.Mapping[str, frozenset[str]] | None = None,
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
    declared : Mapping[str, frozenset[str]] | None, optional
        The lane legs a declared pairing covers, by leg name, with their
        selecting conditions. Their selection is held by the pairing rules,
        not compared here, but they still share the commit pin.

    Returns
    -------
    list[str]
        Every violation of the shared-selection and shared-pin rules.

    """
    generators = action_steps(publisher, COVERAGE_ACTION)
    if not generators:
        return ["the publisher must generate coverage; found no generate-coverage step"]
    measured = [selection(publisher, step) for step in generators]
    lanes = _lane_legs(closure)
    pinned = [*generators, upload_step(publisher), *(leg.step for _, leg in lanes)]
    pins = {pin_of(step) for step in pinned}
    return (
        ratchet_violations(publisher, "the publisher")
        + [
            f"{name}: coverage selection differs from the publisher's"
            for name, leg in lanes
            if leg.ident not in (declared or {})
            and _unpaired(selection(leg.document, leg.step), measured)
        ]
        + _pin_violations(pins)
    )


def _lane_legs(closure: dict[str, Document]) -> list[tuple[str, Leg]]:
    """Return each pull-request generator leg with its workflow's name."""
    return [
        (name, leg)
        for name, document in sorted(closure.items())
        for leg in generator_legs(name, document)
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
