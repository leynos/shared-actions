"""Rules for the shape of the one push-to-main CodeScene publisher.

Each rule returns its findings as text; an empty list is compliance.
"""

from __future__ import annotations

import re
import typing as typ

from .expressions import ConditionError, missing_terms
from .publisher import (
    COVERAGE_ACTION,
    PERMITTED_TRIGGERS,
    PINNED_COMMIT,
    PUBLISHER_GROUPS,
    TOKEN_INPUT,
    UPLOAD_ACTION,
    UPLOAD_GUARD,
    action_steps,
    invokes,
    pin_of,
    position,
    upload_job,
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


def concurrency_violations(document: Document) -> list[str]:
    """Require one ref-keyed, never-cancelling group, at one scope.

    The group may sit on the workflow or on the upload job, never both:
    GitHub treats the same group at both scopes as a deadlock and cancels
    the job. No other job may declare concurrency, since only these two
    scopes govern the upload. `cancel-in-progress` must be the literal
    `false`, so neither an expression nor a later default can cancel a
    pending upload.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the publisher's concurrency requirements.

    """
    held = upload_job(document)
    scopes = [
        (scope, value)
        for scope, value in (
            ("the workflow", document.get("concurrency")),
            ("the upload job", held.get("concurrency")),
        )
        if value is not None
    ]
    found = [
        f"job {name} declares its own concurrency"
        for name, job in jobs(document).items()
        if job is not held and "concurrency" in job
    ]
    if len(scopes) != 1:
        where = " and ".join(scope for scope, _ in scopes) or "no scope"
        return [
            *found,
            f"the publisher group must be declared at one scope; found {where}",
        ]
    scope, value = scopes[0]
    return found + _group_violations(scope, value)


def _group_violations(scope: str, value: object) -> list[str]:
    """Refuse a declaration whose group or cancellation is not the publisher's."""
    if not isinstance(value, dict):
        return [f"{scope} declares concurrency {value!r}, not a group mapping"]
    group = _normalized(value.get("group"))
    message = (
        f"{scope} groups by {value.get('group')!r}, "
        f"not one of {sorted(PUBLISHER_GROUPS)}"
    )
    found = [] if group in PUBLISHER_GROUPS else [message]
    if value.get("cancel-in-progress") is not False:
        found.append(f"{scope} must set cancel-in-progress: false")
    return found


def _normalized(group: object) -> str:
    """Return a group with single spaces inside each `${{ }}`."""
    return re.sub(
        r"\$\{\{\s*(.*?)\s*\}\}",
        lambda match: f"${{{{ {match.group(1)} }}}}",
        str(group),
    )


def _guard_violations(step: dict[str, object]) -> list[str]:
    """Require the ref and availability guard as whole `&&` terms."""
    try:
        missing = missing_terms(step.get("if"), UPLOAD_GUARD)
    except ConditionError as error:
        return [str(error)]
    return [f"the upload guard lacks {term!r}" for term in missing]


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
        f"{name} is {inputs.get(name)!r}, not {wanted!r}"
        for name, wanted in expected.items()
        if inputs.get(name) != wanted
    ]
    if not PINNED_COMMIT.match(pin_of(step)):
        found.append(f"the uploader is not pinned to a commit: {step.get('uses')!r}")
    return found + _guard_violations(step)


def permissions_violations(document: Document) -> list[str]:
    """Require the publisher's workflow-level token to hold no scope.

    Each job then opts in to what it needs; a scope granted at workflow
    level reaches every job, the check and upload steps included.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the publisher's permissions requirement.

    """
    declared = document.get("permissions")
    return (
        [] if declared == {} else [f"workflow permissions are {declared!r}, not {{}}"]
    )


def wiring_violations(document: Document) -> list[str]:
    """Require the upload to read the file, in the format, the publisher writes.

    The report must be written earlier in the upload's own job: a generator
    after the upload, or in another job, leaves the uploader nothing to read
    while every other clause passes. Both ends must name the file: two
    absent inputs compare equal, and the actions' defaults are not read
    here, so an empty reading would otherwise pass.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the upload's wiring to an earlier generator.

    """
    upload = upload_step(document)
    job_steps = steps(upload_job(document))
    earlier = job_steps[: position(job_steps, upload)]
    written = [
        (_input(step, "output-path"), _input(step, "format"))
        for step in earlier
        if invokes(step, COVERAGE_ACTION)
    ]
    read = (_input(upload, "path"), _input(upload, "format"))
    if not _names_a_file(read[0]):
        return ["the upload must name its report with an explicit `path`"]
    return (
        []
        if read in written
        else [f"the upload reads {read!r}; earlier steps of its job write {written!r}"]
    )


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
        for key in SKIPPING_KEYS
        if key in job
    ]
    found += [
        f"the publisher's generate-coverage step carries `{key}`"
        for step in action_steps(document, COVERAGE_ACTION)
        for key in SKIPPING_KEYS
        if key in step
    ]
    return found + [
        "the upload step carries `continue-on-error`"
        for step in action_steps(document, UPLOAD_ACTION)
        if "continue-on-error" in step
    ]


def _input(step: dict[str, object], name: str) -> object:
    """Return one `with` input of a step, or None when it has none."""
    inputs = step.get("with")
    return inputs.get(name) if isinstance(inputs, dict) else None


def _names_a_file(value: object) -> bool:
    """Return whether an input value is a non-empty file name."""
    return isinstance(value, str) and bool(value)


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
        for text in texts(document)
        if re.search(r"installer-checksum|codescene_cli_sha256", text.casefold())
    ]
    return found + [
        f"{name} is the retired checksum refresher"
        for name in documents
        if name.startswith("get-codescene-sha.")
    ]
