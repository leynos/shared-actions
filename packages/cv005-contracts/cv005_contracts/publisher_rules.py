"""Rules for the shape of the one push-to-main CodeScene publisher.

Each rule returns its findings as text; an empty list is compliance.
"""

from __future__ import annotations

import re
import typing as typ

from .expressions import ConditionError, missing_terms
from .publisher import (
    BASELINE_ACTION,
    COVERAGE_ACTION,
    PERMITTED_TRIGGERS,
    PINNED_COMMIT,
    TOKEN_INPUT,
    UPLOAD_ACTION,
    action_steps,
    availability_ids,
    generates_coverage,
    invokes,
    pin_of,
    upload_guard,
    upload_step,
)
from .reach import TRUNK_FILTERS
from .reading import jobs, steps, texts, trigger_filters, triggers

if typ.TYPE_CHECKING:
    from .loading import Document


def trigger_violations(document: Document) -> list[str]:
    """Refuse any trigger but a push to main and an optional dispatch.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the publisher's permitted triggers.

    """
    found = [
        f"trigger {name!r} is not permitted"
        for name in sorted(triggers(document) - PERMITTED_TRIGGERS)
    ]
    if "push" not in triggers(document):
        found.append("the publisher does not run on a push")
    if trigger_filters(document, "push") not in TRUNK_FILTERS:
        found.append("the push trigger must filter on exactly `branches: [main]`")
    return found


def _guard_violations(document: Document) -> list[str]:
    """Require the main-ref guard as a whole `&&` term.

    An availability term is optional, since the uploader records an empty
    token itself. Two availability terms would leave the check step
    ambiguous, so a guard may read at most one.
    """
    try:
        missing = missing_terms(upload_step(document).get("if"), upload_guard(document))
    except ConditionError as error:
        return [str(error)]
    found = [f"the upload guard lacks {term!r}" for term in missing]
    ids = availability_ids(document)
    if len(ids) > 1:
        found.append(
            f"the upload guard reads availability from more than one step: {ids}"
        )
    return found


#: The upload action's own defaults for the inputs the rule pins. An absent
#: `mode` runs the action's default, which is `upload`, so leaving it out is
#: as safe as spelling it; any other value, `check` or an expression, is not.
ACTION_DEFAULTS: typ.Final[dict[str, str]] = {"mode": "upload"}


def upload_step_violations(document: Document) -> list[str]:
    """Require the upload step's mode, pin, guard and direct token input.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the upload step's requirements.

    """
    step = upload_step(document)
    inputs = step.get("with") or {}
    if not isinstance(inputs, dict):
        return ["the upload step's `with` must be a mapping"]
    expected = {
        "mode": "upload",
        "access-token": TOKEN_INPUT,
    }
    found = [
        f"{name} is {inputs.get(name, ACTION_DEFAULTS.get(name))!r}, not {wanted!r}"
        for name, wanted in expected.items()
        if inputs.get(name, ACTION_DEFAULTS.get(name)) != wanted
    ]
    if not PINNED_COMMIT.match(pin_of(step)):
        found.append(f"the uploader is not pinned to a commit: {step.get('uses')!r}")
    return found + _guard_violations(document)


#: Keys that let a job or step be skipped, or fail without failing the run.
SKIPPING_KEYS: typ.Final[tuple[str, ...]] = ("if", "continue-on-error")


def condition_violations(document: Document) -> list[str]:
    """Refuse a condition that could skip the publisher's work on a push.

    A job-level `if:` can skip the whole publisher, and one on the coverage
    step can skip the baseline while the upload guard still reads clean, so
    only the upload step, whose guard is asserted, may carry a condition.
    `continue-on-error` is refused in the same places and on the upload step:
    a failed baseline write or upload would then leave a green run.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the publisher's unconditional-run requirement.

    """
    found = [
        f"job {name} carries `{key}`"
        for name, job in jobs(document).items()
        for key in _skipping_keys(job)
    ]
    found += [
        f"the publisher's generate-coverage step carries `{key}`"
        for step in action_steps(document, COVERAGE_ACTION)
        for key in _skipping_keys(step)
    ]
    return found + [
        "the upload step carries `continue-on-error`"
        for step in action_steps(document, UPLOAD_ACTION)
        if "continue-on-error" in step
    ]


def baseline_publisher_violations(document: Document) -> list[str]:
    """Require a baseline publisher wherever a generator hands its figures on.

    A generator with `publish-baseline: never` neither ratchets nor saves, so
    without a publisher the baseline would stop advancing without any job
    failing. Some other job must `needs` the generator's job and run
    `publish-coverage-baseline` on the artefact the generator names.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every generator that hands over with no publisher to receive it.

    """
    return [
        f"job {name} generates coverage with publish-baseline: never but no job "
        "that needs it runs publish-coverage-baseline on the "
        "baseline-artefact-name it sets"
        for name, job in jobs(document).items()
        for step in steps(job)
        if _hands_over(step) and not _is_received(document, name, step)
    ]


def _hands_over(step: dict[str, object]) -> bool:
    """Return whether a measuring step declines to publish its baseline."""
    return generates_coverage(step) and _input(step, "publish-baseline") == "never"


#: The baseline paths a generator and its publisher must agree on. They are
#: part of a cache entry's version, so a publisher saving other paths saves an
#: entry the pull-request lanes cannot restore.
BASELINE_PATH_INPUTS: typ.Final[tuple[str, ...]] = (
    "baseline-rust-file",
    "baseline-python-file",
)


def _is_received(document: Document, producer: str, step: dict[str, object]) -> bool:
    """Return whether `step`'s hand-over has a sound publisher after it."""
    artefact = _input(step, "baseline-artefact-name")
    return bool(artefact) and any(
        _accepts(step, receiver)
        for receiver in _receivers(document, producer, artefact)
    )


def _receivers(
    document: Document, producer: str, artefact: object
) -> list[dict[str, object]]:
    """Return the publisher steps in jobs needing `producer` for `artefact`."""
    return [
        step
        for name, job in jobs(document).items()
        if name != producer and producer in _needs(job)
        for step in steps(job)
        if invokes(step, BASELINE_ACTION) and _input(step, "artefact-name") == artefact
    ]


def _accepts(generator: dict[str, object], receiver: dict[str, object]) -> bool:
    """Return whether a receiver always runs, fails the job, and fits the paths.

    A step with an `if` or `continue-on-error` could leave the baseline
    unchanged under a green run, and one saving other paths saves an entry no
    lane restores.
    """
    return not _skipping_keys(receiver) and all(
        _input(generator, key) == _input(receiver, key) for key in BASELINE_PATH_INPUTS
    )


def _input(step: dict[str, object], name: str) -> object:
    """Return one `with` input of a step, or None when it has no such input."""
    inputs = step.get("with")
    return inputs.get(name) if isinstance(inputs, dict) else None


def _needs(job: dict[str, object]) -> list[object]:
    """Return a job's `needs` as a list, which YAML allows to be a scalar."""
    needed = job.get("needs") or []
    return needed if isinstance(needed, list) else [needed]


def _skipping_keys(holder: dict[str, object]) -> list[str]:
    """Return the keys a job or step carries that could skip or excuse it."""
    return [key for key in SKIPPING_KEYS if key in holder]


def retired_checksum_violations(documents: dict[str, Document]) -> list[str]:
    """Refuse the retired installer checksum and its refresher anywhere.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.

    Returns
    -------
    list[str]
        Every violation naming the retired checksum or its refresher.

    """
    found = [
        f"{name} names {text!r}"
        for name, document in sorted(documents.items())
        for text in _retired_names(document)
    ]
    return found + [
        f"{name} is the retired checksum refresher"
        for name in documents
        if name.startswith("get-codescene-sha.")
    ]


#: The retired installer checksum input and its repository variable.
RETIRED_CHECKSUM: typ.Final[re.Pattern[str]] = re.compile(
    r"installer-checksum|codescene_cli_sha256"
)


def _retired_names(document: Document) -> list[str]:
    """Return every text in a document naming the retired checksum."""
    return [
        text for text in texts(document) if RETIRED_CHECKSUM.search(text.casefold())
    ]
