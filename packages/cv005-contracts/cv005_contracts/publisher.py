"""Find the one push-to-main workflow allowed to upload to CodeScene.

The publisher is found rather than named, as the only workflow in the tree
that contacts CodeScene at all, so a second uploader cannot hide behind
the first. This module locates it and its steps; `publisher_rules` judges
its shape and `credential` where its secret may appear.
"""

from __future__ import annotations

import re
import typing as typ

from .actions import is_action
from .expressions import ConditionError, conjuncts
from .loading import Document, WorkflowReadingError
from .reach import codescene_contacts
from .reading import jobs, steps

UPLOAD_ACTION: typ.Final[str] = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
)
COVERAGE_ACTION: typ.Final[str] = (
    "leynos/shared-actions/.github/actions/generate-coverage"
)
PINNED_COMMIT: typ.Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
TOKEN_INPUT: typ.Final[str] = "${{ secrets.CS_ACCESS_TOKEN }}"  # noqa: S105 - an expression naming the secret, not one.
#: The check step's id where the upload guard names none to follow.
CHECK_STEP_ID: typ.Final[str] = "codescene-token"
MAIN_REF_GUARD: typ.Final[str] = "github.ref == 'refs/heads/main'"
#: The upload guard's availability term. The estate names the check step
#: more than one way (`codescene-token`, `codescene-credential`), so the
#: step is found through the id this term reads, and that step must then
#: run the exact check command.
AVAILABLE_TERM: typ.Final[re.Pattern[str]] = re.compile(
    r"steps\.(?P<id>[A-Za-z_][A-Za-z0-9_-]*)\.outputs\.available == 'true'"
)

#: The publisher's concurrency groups, held exactly after the spacing inside
#: `${{ }}` is normalized. Keyed on the ref alone, so every run for main
#: shares one group: runs never overlap, and a newer trigger replaces an
#: older pending run. Adding the event name would split main into two
#: groups, letting an earlier dispatch overlap or finish after a newer push
#: and upload older coverage last. The estate spells the workflow part two
#: ways: the file's stem, or `github.workflow`.
PUBLISHER_GROUPS: typ.Final[frozenset[str]] = frozenset(
    {
        "coverage-main-${{ github.ref }}",
        "${{ github.workflow }}-${{ github.ref }}",
    }
)
PERMITTED_TRIGGERS: typ.Final[frozenset[str]] = frozenset({"push", "workflow_dispatch"})


def find_publisher(documents: dict[str, Document]) -> tuple[str, Document]:
    """Return the single workflow that contacts CodeScene.

    Only workflows are candidates. A local action is judged where it runs:
    in the pull-request closure, which refuses any CodeScene contact, or as
    a step of the publisher, whose own rules apply.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.

    Returns
    -------
    tuple[str, Document]
        The publisher's file name and its parsed document.

    Raises
    ------
    WorkflowReadingError
        If none does, or more than one does.

    """
    found = [
        name
        for name, doc in documents.items()
        if not is_action(name) and codescene_contacts(doc)
    ]
    if len(found) != 1:
        message = f"exactly one workflow may contact CodeScene; found {found}"
        raise WorkflowReadingError(message)
    return found[0], documents[found[0]]


def action_steps(document: Document, action: str) -> list[dict[str, object]]:
    """Return every step in a document invoking one action at any ref.

    Parameters
    ----------
    document : Document
        The workflow document to search.
    action : str
        The action reference to match, ignoring its ref.

    Returns
    -------
    list[dict[str, object]]
        Every step invoking `action`, in document order.

    """
    return [
        step
        for job in jobs(document).values()
        for step in steps(job)
        if invokes(step, action)
    ]


def invokes(step: dict[str, object], action: str) -> bool:
    """Return whether a step invokes one action at any ref.

    Parameters
    ----------
    step : dict[str, object]
        The step to inspect.
    action : str
        The action reference to match, ignoring its ref.

    Returns
    -------
    bool
        Whether the step's `uses:` names `action`, case-insensitively.

    """
    return _action_of(step) == action.casefold()


def _action_of(step: dict[str, object]) -> str:
    """Return a step's action reference without its ref, case-folded.

    GitHub resolves the owner and repository without regard to case, so a
    differently cased reference runs the same action.

    Returns
    -------
    str
        The reference before any `@`, case-folded.

    """
    return str(step.get("uses", "")).split("@", 1)[0].casefold()


def pin_of(step: dict[str, object]) -> str:
    """Return the ref after the `@` in a step's `uses:`.

    Parameters
    ----------
    step : dict[str, object]
        The step to inspect.

    Returns
    -------
    str
        The ref after the `@`, or an empty string if there is none.

    """
    return str(step.get("uses", "")).partition("@")[2]


def upload_step(document: Document) -> dict[str, object]:
    """Return the publisher's one upload step.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    dict[str, object]
        The upload step.

    Raises
    ------
    WorkflowReadingError
        If the uploader is invoked other than exactly once.

    """
    found = action_steps(document, UPLOAD_ACTION)
    if len(found) != 1:
        message = f"the publisher must upload exactly once; found {len(found)}"
        raise WorkflowReadingError(message)
    return found[0]


def upload_job(document: Document) -> dict[str, object]:
    """Return the job holding the publisher's one upload step.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    dict[str, object]
        The job holding the upload step.

    """
    step = upload_step(document)
    return next(
        job
        for job in jobs(document).values()
        if any(candidate is step for candidate in steps(job))
    )


def position(job_steps: list[dict[str, object]], step: dict[str, object]) -> int:
    """Return a step's index by identity, since two steps may compare equal.

    Parameters
    ----------
    job_steps : list[dict[str, object]]
        The job's steps, in document order.
    step : dict[str, object]
        The step to locate, by identity.

    Returns
    -------
    int
        The index of `step` within `job_steps`.

    """
    return next(index for index, other in enumerate(job_steps) if other is step)


def availability_ids(document: Document) -> list[str]:
    """Return the check-step ids the upload guard's availability terms read.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Each id read by a whole `steps.<id>.outputs.available == 'true'`
        term, in order; empty when the guard is unreadable.

    """
    try:
        terms = conjuncts(upload_step(document).get("if"))
    except ConditionError:
        return []
    return [
        match.group("id")
        for term in terms
        if (match := AVAILABLE_TERM.fullmatch(term)) is not None
    ]


def check_step_id(document: Document) -> str:
    """Return the id of the step the upload guard reads availability from.

    A guard reading no availability output, or more than one, names no
    step to follow, so the default id is returned and the guard rule
    reports the guard itself.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    str
        The one id the guard reads, or `CHECK_STEP_ID`.

    Examples
    --------
    >>> step = {
    ...     "uses": f"{UPLOAD_ACTION}@main",
    ...     "if": "steps.codescene-credential.outputs.available == 'true'",
    ... }
    >>> check_step_id({"jobs": {"upload": {"steps": [step]}}})
    'codescene-credential'

    """
    match availability_ids(document):
        case [found]:
            return found
        case _:
            return CHECK_STEP_ID


def upload_guard(document: Document) -> frozenset[str]:
    """Return the whole terms the upload step's guard must carry.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    frozenset[str]
        The main-ref term and the availability term for the check step.

    """
    available = f"steps.{check_step_id(document)}.outputs.available == 'true'"
    return frozenset({MAIN_REF_GUARD, available})
