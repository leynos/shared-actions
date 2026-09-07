#!/usr/bin/env -S uv run python
"""Reads one pull request out of GitHub, in the shape the rule needs.

The GitHub boundary of the automerge script. Everything here turns a
GraphQL response into values: the pull request's author, its labels, its
merge state, and the audit over every commit on its branch. The
eligibility rule lives in ``dependabot_commit_audit`` and the verdict in
``dependabot_decision``, neither of which touches a network.

The call made against GitHub is a required parameter rather than a
module attribute. This module therefore names no client of its own, and
a caller supplies the one it wants instead of every caller in the
process sharing whichever was patched last.

Separated from ``dependabot_automerge`` so the transport and the
workflow's own orchestration are read apart.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

if __package__:
    from .dependabot_commit_audit import (
        AUTHOR_PAGE_SIZE,
        COMMIT_PAGE_SIZE,
        MAX_COMMIT_PAGES,
        CommitAudit,
        commit_page,
        foreign_commits,
    )
    from .dependabot_decision import PullRequestContext
    from .dependabot_merge_state import (
        MergeableState,
        MergeStateStatus,
    )
    from .dependabot_queries import COMMITS_PAGE_QUERY, PULL_REQUEST_QUERY
    from .graphql_client import JsonValue
    from .output import fail
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        AUTHOR_PAGE_SIZE,
        COMMIT_PAGE_SIZE,
        MAX_COMMIT_PAGES,
        CommitAudit,
        commit_page,
        foreign_commits,
    )
    from dependabot_decision import (  # type: ignore[import-not-found,no-redef]
        PullRequestContext,
    )
    from dependabot_merge_state import (  # type: ignore[import-not-found,no-redef]
        MergeableState,
        MergeStateStatus,
    )
    from dependabot_queries import (  # type: ignore[import-not-found,no-redef]
        COMMITS_PAGE_QUERY,
        PULL_REQUEST_QUERY,
    )
    from graphql_client import JsonValue  # type: ignore[import-not-found,no-redef]
    from output import fail  # type: ignore[import-not-found,no-redef]

#: The one call this module makes against GitHub. Named so it can be
#: supplied rather than reached for.
type GraphQLQuery = cabc.Callable[
    [str, str, dict[str, JsonValue]], dict[str, JsonValue]
]


def extract_author_login(pull_request: dict[str, JsonValue]) -> str:
    """Extract author login from PR data, returning empty string if unavailable."""
    author_obj = pull_request.get("author")
    if not isinstance(author_obj, dict):
        return ""
    login = author_obj.get("login")
    if not isinstance(login, str):
        return ""
    return login


def extract_labels(pull_request: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract label names from PR data, returning empty tuple if unavailable."""
    labels_obj = pull_request.get("labels")
    if not isinstance(labels_obj, dict):
        return ()
    nodes = labels_obj.get("nodes")
    if not isinstance(nodes, list):
        return ()
    labels: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if isinstance(name, str):
            labels.append(name)
    return tuple(labels)


def _normalize_enum(value: JsonValue | None) -> str | None:
    """Normalize a GraphQL enum value to uppercase string form."""
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def _extract_enum[EnumT](
    data: dict[str, JsonValue],
    field: str,
    enum_type: type[EnumT],
    default: EnumT,
) -> EnumT:
    """Extract and validate an enum field from GraphQL data."""
    normalized = _normalize_enum(data.get(field))
    if normalized is None:
        return default
    try:
        return enum_type(normalized)
    except ValueError:
        return default


class PullRequestRef(typ.NamedTuple):
    """Where a pull request lives, as every query here needs it.

    The three travel together through the fetch and paging path, so they
    travel as one value rather than as three parameters repeated at each
    call.

    Attributes
    ----------
    owner : str
        The repository owner.
    repo : str
        The repository name.
    number : int
        The pull request number.
    """

    owner: str
    repo: str
    number: int

    def __str__(self) -> str:
        """Return ``owner/repo#number``.

        Returns
        -------
        str
            The pull request's location, for a message.
        """
        return f"{self.owner}/{self.repo}#{self.number}"


def commit_variables(ref: PullRequestRef, cursor: str | None) -> dict[str, JsonValue]:
    """Build the variables both commit-bearing queries take.

    Parameters
    ----------
    ref : PullRequestRef
        The pull request to query.
    cursor : str or None
        Where to resume the commit connection, or None for the first page.

    Returns
    -------
    dict
        The GraphQL variables.
    """
    return {
        "owner": ref.owner,
        "name": ref.repo,
        "number": ref.number,
        "commitPageSize": COMMIT_PAGE_SIZE,
        "authorPageSize": AUTHOR_PAGE_SIZE,
        "commitCursor": cursor,
    }


def pull_request_node(
    data: dict[str, JsonValue], ref: PullRequestRef
) -> dict[str, JsonValue]:
    """Unwrap the pull request node, failing when it is absent.

    Parameters
    ----------
    data : dict
        A GraphQL response body.
    ref : PullRequestRef
        The pull request that was queried, for the failure message.

    Returns
    -------
    dict
        The pull request node.
    """
    repository = data.get("repository")
    if isinstance(repository, dict):
        pull_request = repository.get("pullRequest")
        if isinstance(pull_request, dict):
            return pull_request
    # `fail` is NoReturn; returning it is what tells the linter so, since
    # the conditional import leaves that annotation out of reach here.
    return fail(f"Pull request {ref} was not found.")


def audit_whole_branch(
    token: str,
    ref: PullRequestRef,
    first_page: dict[str, JsonValue],
    *,
    query: GraphQLQuery,
) -> CommitAudit:
    """Audit every commit on the branch, not merely the first page.

    A connection read to its page size and no further looks complete: the
    nodes come back, the audit runs, and a commit past the limit is never
    seen. On a branch of more than :data:`COMMIT_PAGE_SIZE` commits that
    is a foreign commit certified as Dependabot's, so the pages are
    followed to the end.

    Parameters
    ----------
    token : str
        A GitHub token.
    ref : PullRequestRef
        The pull request being audited.
    first_page : dict
        The pull request node already fetched, carrying page one.
    query : GraphQLQuery or None
        The call used to read each further page. Defaults to the live
        client, so a caller that wants a different one supplies it
        rather than patching this module.

    Returns
    -------
    CommitAudit
        The audit over every commit, or an unreadable result when a page
        carried no commit list or the branch exceeded
        :data:`MAX_COMMIT_PAGES`.
    """
    page = commit_page(first_page)
    if page is None:
        return CommitAudit(readable=False, foreign=())
    records = list(page.records)
    cursor = page.next_cursor
    pages = 1
    while cursor is not None:
        if pages >= MAX_COMMIT_PAGES:
            return CommitAudit(readable=False, foreign=())
        data = query(
            token,
            COMMITS_PAGE_QUERY,
            commit_variables(ref, cursor),
        )
        node = pull_request_node(data, ref)
        page = commit_page(node)
        if page is None:
            return CommitAudit(readable=False, foreign=())
        records.extend(page.records)
        cursor = page.next_cursor
        pages += 1
    return CommitAudit(
        readable=True,
        foreign=foreign_commits(records),
        pages=pages,
        commits=len(records),
    )


def fetch_pull_request(
    token: str,
    ref: PullRequestRef,
    *,
    query: GraphQLQuery,
) -> PullRequestContext:
    """Fetch PR metadata from the GitHub GraphQL API.

    Parameters
    ----------
    token : str
        A GitHub token.
    owner : str
        The repository's owner.
    repo : str
        The repository's name.
    number : int
        The pull request's number.
    query : GraphQLQuery or None
        The call used to read the pull request and each further commit
        page. Defaults to the live client.

    Returns
    -------
    PullRequestContext
        The pull request as the decision layer reads it.
    """
    data = query(token, PULL_REQUEST_QUERY, commit_variables(ref, None))
    pull_request = pull_request_node(data, ref)
    author_login = extract_author_login(pull_request)
    labels = extract_labels(pull_request)
    audit = audit_whole_branch(token, ref, pull_request, query=query)
    auto_merge_enabled = pull_request.get("autoMergeRequest") is not None
    node_id = pull_request.get("id")
    head_oid = pull_request.get("headRefOid")
    return PullRequestContext(
        number=ref.number,
        owner=ref.owner,
        repo=ref.repo,
        author=author_login,
        is_draft=bool(pull_request.get("isDraft", False)),
        labels=labels,
        node_id=node_id if isinstance(node_id, str) else None,
        head_oid=head_oid if isinstance(head_oid, str) else None,
        auto_merge_enabled=auto_merge_enabled,
        merge_state_status=_extract_enum(
            pull_request,
            "mergeStateStatus",
            MergeStateStatus,
            MergeStateStatus.UNKNOWN,
        ),
        mergeable_state=_extract_enum(
            pull_request,
            "mergeable",
            MergeableState,
            MergeableState.UNKNOWN,
        ),
        foreign_commits=audit.foreign,
        commits_readable=audit.readable,
        commit_pages_read=audit.pages,
        commits_audited=audit.commits,
    )
