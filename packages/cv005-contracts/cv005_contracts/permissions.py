"""Hold the token scope the publisher's jobs inherit from the workflow.

A job's own `permissions` replaces the workflow's, so only a job declaring
none is judged here; the upload job's own scope is held by the hardening
rules.
"""

from __future__ import annotations

import typing as typ

from .reading import jobs

if typ.TYPE_CHECKING:
    from .loading import Document


def permissions_violations(document: Document) -> list[str]:
    """Require every publisher job to run with a declared scope that cannot write.

    A job's own `permissions` replaces the workflow's, so a workflow-level
    grant reaches only the jobs that declare none. Those jobs run with the
    repository's default token when the workflow declares nothing, which
    may write, and with every write scope the workflow grants otherwise.
    A job that declares its own scope is held elsewhere, the upload job by
    `publisher.least-privilege`.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        One violation for each job left to the default token or to a
        workflow-level write grant.

    """
    declared = document.get("permissions")
    inheriting = [
        name for name, job in jobs(document).items() if "permissions" not in job
    ]
    if declared is None:
        return [
            f"job {name} declares no permissions and runs with the default token"
            for name in inheriting
        ]
    if _grants_write(declared):
        return [
            f"job {name} inherits the workflow's write grant {declared!r}"
            for name in inheriting
        ]
    return []


def _grants_write(declared: object) -> bool:
    """Return whether a `permissions` value grants any write scope.

    Examples
    --------
    >>> [_grants_write(v) for v in ({}, "read-all", {"id-token": "write"})]
    [False, False, True]

    """
    if isinstance(declared, dict):
        return any(value != "read" and value != "none" for value in declared.values())
    return declared != "read-all"
