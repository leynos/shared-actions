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
CHECK_STEP_ID: typ.Final[str] = "codescene-token"
MAIN_REF_GUARD: typ.Final[str] = "github.ref == 'refs/heads/main'"
AVAILABLE_GUARD: typ.Final[str] = f"steps.{CHECK_STEP_ID}.outputs.available == 'true'"
UPLOAD_GUARD: typ.Final[frozenset[str]] = frozenset({MAIN_REF_GUARD, AVAILABLE_GUARD})

#: The publisher's one concurrency declaration, held exactly. Keyed on the
#: ref alone, so every run for main shares one group: runs never overlap,
#: and a newer trigger replaces an older pending run. Adding the event name
#: would split main into two groups, letting an earlier dispatch overlap or
#: finish after a newer push and upload older coverage last.
PUBLISHER_CONCURRENCY: typ.Final[dict[str, object]] = {
    "group": "coverage-main-${{ github.ref }}",
    "cancel-in-progress": False,
}
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
