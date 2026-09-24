"""The pull-request clauses of the main-owned coverage boundary, over a closure.

Each function takes parsed workflows and names the pull-request-reachable
ones that break a clause. They are functions over documents, not assertions
over this repository, so that the probe that found the closure hole can be
built and fed to the same code the contract runs: a rule proved only against
the compliant files it guards passes whether or not it works.

Every clause runs over :func:`workflow_boundary.pull_request_reachable`, the
closure through local reusable-workflow calls. A clause that enumerated only
the workflows a pull request starts directly would share one blind spot with
every other clause: a ``workflow_call``-only file called with ``secrets:
inherit`` holds the credential and curls the host, and nothing looks at it.
"""

from __future__ import annotations

import typing as typ

from .workflow_boundary import (
    CODESCENE_CREDENTIAL,
    CODESCENE_SERVICE_COMMANDS,
    _jobs,
    _steps,
    effective_text,
    names_the_codescene_host,
    pull_request_reachable,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .test_coverage_timeout_tiers import WorkflowDocument

#: The ``secrets:`` value that forwards every secret the caller holds.
INHERIT: typ.Final[str] = "inherit"
#: The self-repository prefix that must not carry an ``@ref``.
SELF_REPOSITORY_PREFIX: typ.Final[str] = "$/"


def credential_holders(documents: cabc.Mapping[str, WorkflowDocument]) -> list[str]:
    """Name the reachable workflows that reference the CodeScene credential.

    The reading covers every place a reference can sit: a ``run:`` body, an
    action input, an environment value or key at any scope, and a named
    ``secrets:`` forward to a called workflow.

    Examples
    --------
    >>> credential_holders(
    ...     {"ci.yml": {True: "pull_request", "env": {"CS_ACCESS_TOKEN": "x"}}}
    ... )
    ['ci.yml']
    """
    return sorted(
        name
        for name in pull_request_reachable(documents)
        if CODESCENE_CREDENTIAL in effective_text(documents[name])
    )


def host_namers(documents: cabc.Mapping[str, WorkflowDocument]) -> list[str]:
    """Name the reachable workflows that can reach the CodeScene host."""
    return sorted(
        name
        for name in pull_request_reachable(documents)
        if names_the_codescene_host(documents[name])
    )


def service_callers(
    documents: cabc.Mapping[str, WorkflowDocument],
) -> dict[str, list[str]]:
    """Map each reachable workflow to the CodeScene service commands it runs."""
    found = {
        name: [
            command
            for command in CODESCENE_SERVICE_COMMANDS
            if command in effective_text(documents[name])
        ]
        for name in sorted(pull_request_reachable(documents))
    }
    return {name: commands for name, commands in found.items() if commands}


def secret_inheritors(documents: cabc.Mapping[str, WorkflowDocument]) -> list[str]:
    """Name each reachable job that forwards every secret with ``inherit``.

    ``secrets: inherit`` hands the credential over without naming it, so the
    caller reads clean to :func:`credential_holders`. A local callee is in the
    closure and is read too, but a callee in another repository is code this
    contract cannot see, and one that picks a secret by an expression names
    nothing a substring search can find. A pull-request job forwards the
    secrets it needs by name, where the reading sees them.

    Examples
    --------
    >>> secret_inheritors(
    ...     {"ci.yml": {True: "pull_request", "jobs": {"a": {"secrets": "inherit"}}}}
    ... )
    ['ci.yml:a']
    """
    return sorted(
        f"{name}:{job_name}"
        for name in pull_request_reachable(documents)
        for job_name, job in _jobs(documents[name]).items()
        if str(job.get("secrets", "")).strip() == INHERIT
    )


def _uses_values(document: cabc.Mapping[typ.Any, typ.Any]) -> cabc.Iterator[str]:
    """Yield every ``uses:`` in a workflow, at job and at step level."""
    for job in _jobs(document).values():
        yield str(job.get("uses", ""))
        yield from (str(step.get("uses", "")) for step in _steps(job))


def refused_self_references(
    documents: cabc.Mapping[str, WorkflowDocument],
) -> list[str]:
    """Name every ``$/`` reference that carries an ``@ref``, in any workflow.

    ``$/`` resolves to the running commit, and GitHub documents it without a
    ref. The closure still reads such a call as local, so its callee stays
    inside every clause; this names it as well, so the spelling is fixed
    rather than relied on.

    Examples
    --------
    >>> refused_self_references(
    ...     {"ci.yml": {"jobs": {"a": {"uses": "$/.github/workflows/b.yml@main"}}}}
    ... )
    ['ci.yml: $/.github/workflows/b.yml@main']
    """
    return sorted(
        f"{name}: {uses}"
        for name, document in documents.items()
        for uses in _uses_values(document)
        if uses.startswith(SELF_REPOSITORY_PREFIX) and "@" in uses
    )
