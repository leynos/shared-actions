"""Hold the publisher to one ref-keyed concurrency group that never cancels.

Every run for main shares one group, so runs never overlap and a newer
trigger replaces an older pending run; the group sits at one governing
scope, and no pending upload can be cancelled.
"""

from __future__ import annotations

import re
import typing as typ

from .publisher import PUBLISHER_GROUPS, upload_job
from .reading import jobs

if typ.TYPE_CHECKING:
    from .loading import Document


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
    found = _other_job_groups(document)
    match _declared_scopes(document):
        case [(scope, value)]:
            return found + _group_violations(scope, value)
        case scopes:
            where = " and ".join(scope for scope, _ in scopes) or "no scope"
            message = (
                f"the publisher group must be declared at one scope; found {where}"
            )
            return [*found, message]


def _declared_scopes(document: Document) -> list[tuple[str, object]]:
    """Return the governing scopes, workflow and upload job, that declare a group."""
    held = upload_job(document)
    candidates = (
        ("the workflow", document.get("concurrency")),
        ("the upload job", held.get("concurrency")),
    )
    return [(scope, value) for scope, value in candidates if value is not None]


def _other_job_groups(document: Document) -> list[str]:
    """Report every job but the upload job that declares its own concurrency."""
    held = upload_job(document)
    return [
        f"job {name} declares its own concurrency"
        for name, job in jobs(document).items()
        if job is not held and "concurrency" in job
    ]


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
