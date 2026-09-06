#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "httpx>=0.28,<0.29"]
# ///

"""Enable GitHub auto-merge for eligible Dependabot pull requests.

This script evaluates pull requests against eligibility rules and enables
GitHub's auto-merge feature for qualifying Dependabot PRs. It is designed
to run in GitHub Actions workflows via `workflow_call`.

Eligibility Rules
-----------------
Auto-merge is enabled only when all conditions are met:

- The PR author is ``dependabot[bot]`` or ``dependabot``
- The PR is not a draft
- The required label (default: ``dependencies``) is present

Merge-state handling: auto-merge is armed while the PR is blocked by
required rules (``BLOCKED``). If the PR is already mergeable (``CLEAN``,
``HAS_HOOKS``, or ``UNSTABLE``), GitHub rejects enabling auto-merge, so the
PR is merged directly instead — matching what auto-merge itself would do,
because all required rules are already satisfied. Repositories should
configure required status checks (branch protection or rulesets) so that CI
genuinely gates merging.

Environment Variables
---------------------
INPUT_GITHUB_TOKEN : str
    GitHub token with ``contents:write`` and ``pull-requests:write`` permissions.
INPUT_MERGE_METHOD : str, optional
    Merge method: ``squash``, ``merge``, or ``rebase``. Default: ``squash``.
INPUT_REQUIRED_LABEL : str, optional
    Label required on the PR. Default: ``dependencies``.
INPUT_DRY_RUN : bool, optional
    If ``true``, logs the decision without calling the GitHub API.
INPUT_PULL_REQUEST_NUMBER : int, optional
    PR number override for workflow_call contexts.
INPUT_REPOSITORY : str, optional
    Repository override in ``owner/repo`` form.

Usage
-----
As a standalone script::

    INPUT_GITHUB_TOKEN=ghp_... uv run dependabot_automerge.py

In a GitHub Actions workflow::

    - uses: ./.github/workflows/dependabot-automerge.yml
      with:
        pull-request-number: ${{ github.event.pull_request.number }}

Side Effects
------------
When not in dry-run mode, this script calls the GitHub GraphQL API to enable
auto-merge on the target pull request, or to merge it directly when it is
already in a mergeable state.

See Also
--------
main : The CLI entrypoint function.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import math
import os
import time
import typing as typ
from pathlib import Path
from types import MappingProxyType

from cyclopts import App, Parameter

if __package__:
    from .graphql_client import JsonValue, request_graphql
    from .output import emit, fail
else:
    from graphql_client import (  # type: ignore[import-not-found,no-redef]
        JsonValue,
        request_graphql,
    )
    from output import emit, fail  # type: ignore[import-not-found,no-redef]


class DependabotLogin(enum.StrEnum):
    """Supported Dependabot author login variants.

    Attributes
    ----------
    BOT : DependabotLogin
        The canonical Dependabot bot login (``dependabot[bot]``).
    LEGACY : DependabotLogin
        The legacy Dependabot login (``dependabot``).
    """

    BOT = "dependabot[bot]"
    LEGACY = "dependabot"


DEPENDABOT_LOGINS: frozenset[str] = frozenset(login.value for login in DependabotLogin)

#: Stands in for a commit author GitHub did not name, so a message can
#: still identify the commit.
UNKNOWN_AUTHOR: typ.Final[str] = "an unnamed author"

#: Stands in for the authors of a commit whose credit list came back
#: truncated. Such a commit cannot be certified as Dependabot's, so it is
#: reported as foreign rather than waved through.
UNREAD_CO_AUTHOR: typ.Final[str] = "an unread co-author"

#: How many commits and credited authors one page of the query asks for.
#: Both connections are paged or checked for truncation, because a
#: connection read to its limit and no further is how a foreign commit
#: slips past a check that looks complete.
COMMIT_PAGE_SIZE: typ.Final[int] = 100
AUTHOR_PAGE_SIZE: typ.Final[int] = 100

#: Ceiling on commit pages. Fifty pages is 5,000 commits, far beyond any
#: real dependency branch; stopping there bounds the work rather than
#: trusting a cursor to terminate, and the branch is reported unreadable.
MAX_COMMIT_PAGES: typ.Final[int] = 50


class MergeStateStatus(enum.StrEnum):
    """Supported merge state statuses from GitHub GraphQL."""

    BEHIND = "BEHIND"
    BLOCKED = "BLOCKED"
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    DRAFT = "DRAFT"
    HAS_HOOKS = "HAS_HOOKS"
    MERGED = "MERGED"
    UNKNOWN = "UNKNOWN"
    UNSTABLE = "UNSTABLE"


class MergeableState(enum.StrEnum):
    """Supported mergeable states from GitHub GraphQL."""

    CONFLICTING = "CONFLICTING"
    MERGEABLE = "MERGEABLE"
    UNKNOWN = "UNKNOWN"


MERGE_METHODS = {
    "merge": "MERGE",
    "rebase": "REBASE",
    "squash": "SQUASH",
}

#: The commit connection, shared by the first query and the paging one so
#: the two cannot describe different shapes. ``totalCount`` on ``authors``
#: is what makes a truncated credit list visible.
COMMITS_FRAGMENT = """
      commits(first: $commitPageSize, after: $commitCursor) {
        totalCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          commit {
            oid
            authors(first: $authorPageSize) {
              totalCount
              nodes {
                user {
                  login
                }
              }
            }
          }
        }
      }
"""

PULL_REQUEST_QUERY = (
    """
query PullRequestInfo(
  $owner: String!
  $name: String!
  $number: Int!
  $commitPageSize: Int!
  $authorPageSize: Int!
  $commitCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      number
      isDraft
      mergeStateStatus
      mergeable
      author {
        login
      }
      labels(first: 100) {
        nodes {
          name
        }
      }
      autoMergeRequest {
        enabledAt
        mergeMethod
      }
"""
    + COMMITS_FRAGMENT
    + """
    }
  }
}
"""
)

COMMITS_PAGE_QUERY = (
    """
query PullRequestCommits(
  $owner: String!
  $name: String!
  $number: Int!
  $commitPageSize: Int!
  $authorPageSize: Int!
  $commitCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
"""
    + COMMITS_FRAGMENT
    + """
    }
  }
}
"""
)

ENABLE_AUTOMERGE_MUTATION = """
mutation EnableAutomerge($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(
    input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}
  ) {
    pullRequest {
      number
    }
  }
}
"""

DISABLE_AUTOMERGE_MUTATION = """
mutation DisableAutomerge($pullRequestId: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      number
    }
  }
}
"""

MERGE_PULL_REQUEST_MUTATION = """
mutation MergePullRequest($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  mergePullRequest(
    input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}
  ) {
    pullRequest {
      number
      merged
    }
  }
}
"""

app = App()


class CommitRecord(typ.NamedTuple):
    """One commit on the branch, in terms the eligibility rule uses.

    The GraphQL shape stops here. Everything downstream reads a commit as
    an identifier and the logins credited on it, so the rule can be stated
    and tested without a GitHub response in the way.

    Attributes
    ----------
    oid : str
        The commit SHA.
    authors : tuple[str, ...]
        The logins credited on the commit, with :data:`UNKNOWN_AUTHOR` for
        any GitHub did not name.
    authors_complete : bool
        Whether every credited author was returned. A truncated list
        cannot certify the commit as Dependabot's.
    """

    oid: str
    authors: tuple[str, ...]
    authors_complete: bool = True


class CommitPage(typ.NamedTuple):
    """One page of a branch's commits, and where the next one starts.

    Attributes
    ----------
    records : tuple[CommitRecord, ...]
        The commits on this page.
    next_cursor : str or None
        The cursor for the following page, or None at the end.
    """

    records: tuple[CommitRecord, ...]
    next_cursor: str | None


class CommitAudit(typ.NamedTuple):
    """The outcome of reading a branch's commit authorship.

    Attributes
    ----------
    readable : bool
        Whether the API returned a commit list at all.
    foreign : tuple[ForeignCommit, ...]
        Commits Dependabot did not write.
    """

    readable: bool
    foreign: tuple[ForeignCommit, ...]


class ForeignCommit(typ.NamedTuple):
    """A commit on the branch that Dependabot did not write.

    Attributes
    ----------
    oid : str
        The commit SHA.
    author : str
        The author's login, or ``unknown`` when the API did not name one.
    """

    oid: str
    author: str

    def __str__(self) -> str:
        """Return a short description naming the commit and its author.

        Returns
        -------
        str
            ``<short sha> by <author>``.
        """
        return f"{self.oid[:8]} by {self.author}"


@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Snapshot of the pull request metadata used for gating.

    Attributes
    ----------
    number : int
        The pull request number.
    owner : str
        The repository owner (organization or user).
    repo : str
        The repository name.
    author : str
        The login of the pull request author.
    is_draft : bool
        Whether the pull request is a draft.
    labels : tuple[str, ...]
        Labels currently applied to the pull request.
    node_id : str or None
        The GraphQL node ID for mutations. None when created from event data.
    auto_merge_enabled : bool
        Whether auto-merge is already enabled on this PR.
    merge_state_status : MergeStateStatus
        The PR merge state status (e.g. CLEAN, UNSTABLE), or ``UNKNOWN``.
    mergeable_state : MergeableState
        The PR mergeable state (e.g. MERGEABLE, CONFLICTING), or ``UNKNOWN``.
    foreign_commits : tuple[ForeignCommit, ...]
        Commits on the branch that Dependabot did not write.
    commits_readable : bool
        Whether the commit list could be read. False means the check did
        not run and eligibility rests on the author alone.
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None = None
    auto_merge_enabled: bool = False
    merge_state_status: MergeStateStatus = MergeStateStatus.UNKNOWN
    mergeable_state: MergeableState = MergeableState.UNKNOWN
    foreign_commits: tuple[ForeignCommit, ...] = ()
    commits_readable: bool = True


MERGE_STATE_SKIP_REASONS: typ.Mapping[MergeStateStatus, str] = MappingProxyType(
    {
        MergeStateStatus.DIRTY: "merge-state-dirty",
        MergeStateStatus.BEHIND: "merge-state-behind",
        MergeStateStatus.MERGED: "already-merged",
    }
)
# States where the PR is already mergeable. GitHub rejects
# enablePullRequestAutoMerge here ("Pull request is in clean/unstable
# status"), so the PR is merged directly instead — mirroring what
# auto-merge would do, since all *required* rules are already satisfied.
MERGE_STATE_DIRECT_MERGE: frozenset[MergeStateStatus] = frozenset(
    {
        MergeStateStatus.CLEAN,
        MergeStateStatus.HAS_HOOKS,
        MergeStateStatus.UNSTABLE,
    }
)
MERGEABLE_SKIP_REASONS: typ.Mapping[MergeableState, str] = MappingProxyType(
    {
        MergeableState.CONFLICTING: "mergeable-conflicting",
    }
)
MERGE_STATE_RETRYABLE: frozenset[MergeStateStatus] = frozenset(
    {MergeStateStatus.UNKNOWN}
)
MERGEABLE_RETRYABLE: frozenset[MergeableState] = frozenset({MergeableState.UNKNOWN})
MERGE_STATE_MAX_ATTEMPTS_DEFAULT: int = 3
MERGE_STATE_BASE_SLEEP_DEFAULT: float = 2.0
MERGE_STATE_MAX_SLEEP_DEFAULT: float = 30.0
MERGE_STATE_MAX_ATTEMPTS_ENV: str = "AUTOMERGE_MERGE_STATE_MAX_ATTEMPTS"
MERGE_STATE_BASE_SLEEP_ENV: str = "AUTOMERGE_MERGE_STATE_BASE_SLEEP_SECONDS"
MERGE_STATE_MAX_SLEEP_ENV: str = "AUTOMERGE_MERGE_STATE_MAX_SLEEP_SECONDS"

type MergeStateClassification = typ.Literal["ok", "merge", "skip", "retry"]


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """Decision describing whether auto-merge should proceed.

    Attributes
    ----------
    status : str
        The decision status: ``skipped``, ``cancelled``, ``ready``,
        ``enabled``, ``merged``, or ``error``. ``cancelled`` is a skip that
        also withdrew an auto-merge request armed earlier, and is reported
        separately because the run changed the pull request rather than
        merely declining to.
    reason : str
        Human-readable reason for the decision, e.g. ``author-not-dependabot``.
    """

    status: str
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class AutomergeConfig:
    """Configuration for emitting automerge decisions.

    Attributes
    ----------
    merge_method : str
        The normalized merge method (``SQUASH``, ``MERGE``, or ``REBASE``).
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.
    dry_run : bool
        If True, decisions are logged without calling the GitHub API.
    """

    merge_method: str
    required_label: str | None
    dry_run: bool


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Runtime context for PR evaluation.

    Attributes
    ----------
    repo_full_name : str
        The full repository name in ``owner/repo`` format.
    event : dict or None
        The parsed GitHub event payload, or None if unavailable.
    pull_request_number : int or None
        Explicit PR number override, or None to resolve from event.
    """

    repo_full_name: str
    event: dict[str, JsonValue] | None
    pull_request_number: int | None


def _load_event() -> dict[str, JsonValue] | None:
    """Load the GitHub event payload from GITHUB_EVENT_PATH if set."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Failed to parse event payload: {exc}")


def _normalize_label(required_label: str | None) -> str | None:
    """Strip whitespace from a label, returning None if empty."""
    if required_label is None:
        return None
    label = required_label.strip()
    return label or None


def _normalize_merge_method(merge_method: str) -> str:
    """Validate and normalize a merge method to its GraphQL enum value."""
    normalized = merge_method.strip().lower()
    if normalized not in MERGE_METHODS:
        allowed = ", ".join(sorted(MERGE_METHODS))
        fail(f"Invalid merge_method '{merge_method}'. Allowed: {allowed}.")
    return MERGE_METHODS[normalized]


def _get_repo_from_event(event: dict[str, JsonValue] | None) -> str:
    """Return the repository full name from an event payload when available."""
    if not event:
        return ""
    repository = event.get("repository")
    if not isinstance(repository, dict):
        return ""
    full_name = repository.get("full_name")
    if isinstance(full_name, str):
        return full_name.strip()
    return ""


def _resolve_repository(
    repository: str | None, event: dict[str, JsonValue] | None
) -> str:
    """Resolve the repository from input, event payload, or environment."""
    if repository and (candidate := repository.strip()):
        return candidate
    if repo := _get_repo_from_event(event):
        return repo
    if repo := os.environ.get("GITHUB_REPOSITORY"):
        return repo
    fail("Repository not provided. Set INPUT_REPOSITORY or GITHUB_REPOSITORY.")
    raise AssertionError  # unreachable


def _split_repo(full_name: str) -> tuple[str, str]:
    """Split a full repository name into owner and repo components."""
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        fail(f"Repository '{full_name}' must be in owner/repo form.")
    return parts[0], parts[1]


def _resolve_pull_request_number(
    pull_request_number: int | None, event: dict[str, JsonValue] | None
) -> int:
    """Resolve the PR number from input or event payload."""
    if pull_request_number is not None:
        return pull_request_number
    if event and (number := event.get("pull_request", {}).get("number")) is not None:
        try:
            return int(number)
        except (TypeError, ValueError):
            pass
    fail(
        "Pull request number not provided. Set INPUT_PULL_REQUEST_NUMBER or include "
        "it in the event payload."
    )
    raise AssertionError  # unreachable


def _parse_pr_number(pr: dict[str, JsonValue]) -> int:
    """Parse and validate the pull request number from event payload data."""
    number = pr.get("number")
    if number is None:
        fail("Event payload missing pull_request.number.")
    try:
        return int(number)
    except (TypeError, ValueError):
        fail("Event payload pull_request.number is not an integer.")


def _labels_from_pr(pr: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract label names from a validated pull_request dict."""
    labels = []
    for label in pr.get("labels") or []:
        if not isinstance(label, dict):
            continue
        name = label.get("name")
        if isinstance(name, str) and name:
            labels.append(name)
    return tuple(labels)


def _snapshot_from_event(
    event: dict[str, JsonValue], repo_full_name: str
) -> PullRequestContext:
    """Build a PullRequestContext from a GitHub event payload."""
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        fail("Event payload does not include pull_request data.")
    pr_number = _parse_pr_number(pr)
    author = pr.get("user", {}).get("login")
    author_login = author if isinstance(author, str) else ""
    is_draft = bool(pr.get("draft", False))
    owner, repo = _split_repo(repo_full_name)
    return PullRequestContext(
        number=pr_number,
        owner=owner,
        repo=repo,
        author=author_login,
        is_draft=is_draft,
        labels=_labels_from_pr(pr),
    )


def _evaluate(pr: PullRequestContext, required_label: str | None) -> Decision:
    """Evaluate a PR against eligibility rules and return a Decision.

    Dependabot eligibility accepts authors ``dependabot[bot]`` and
    ``dependabot`` as defined by :data:`DEPENDABOT_LOGINS`, which includes both
    author variants.

    Parameters
    ----------
    pr : PullRequestContext
        Snapshot of pull request metadata used for eligibility checks.
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.

    Returns
    -------
    Decision
        Outcome indicating whether auto-merge should proceed.

    Notes
    -----
    :data:`DEPENDABOT_LOGINS` is the canonical source of eligible Dependabot
    author logins used by this evaluation.

    """
    if pr.author not in DEPENDABOT_LOGINS:
        return Decision(status="skipped", reason="author-not-dependabot")
    if pr.foreign_commits:
        # Opening the pull request is not the same as writing what is in
        # it. Once Dependabot opens one, anything pushed to that branch
        # would otherwise merge under this rule without review, which is
        # how a workflow edit reached a trunk unreviewed.
        return Decision(
            status="skipped",
            reason=f"foreign-commit:{pr.foreign_commits[0].oid[:8]}",
        )
    if pr.is_draft:
        return Decision(status="skipped", reason="draft-pr")
    if required_label and required_label not in pr.labels:
        return Decision(status="skipped", reason=f"missing-label:{required_label}")
    return Decision(status="ready", reason="eligible")


def _emit_decision(
    pr: PullRequestContext,
    decision: Decision,
    *,
    config: AutomergeConfig,
) -> None:
    """Emit structured decision output for the automerge workflow."""
    reason = decision.reason
    if decision.status == "ready" and config.dry_run:
        status = "dry-run"
    else:
        status = decision.status
    emit("automerge_status", status)
    emit("automerge_reason", reason)
    emit("automerge_merge_method", config.merge_method)
    emit("automerge_required_label", config.required_label or "")
    emit("automerge_repository", f"{pr.owner}/{pr.repo}")
    emit("automerge_pr_number", pr.number)
    emit("automerge_author", pr.author)
    emit("automerge_draft", str(pr.is_draft).lower())
    emit("automerge_labels", pr.labels)
    emit("automerge_merge_state", pr.merge_state_status.value)
    emit("automerge_mergeable_state", pr.mergeable_state.value)
    emit("automerge_commit_audit", _commit_audit_outcome(pr))
    if pr.foreign_commits:
        _announce_foreign_commits(pr)
    elif not pr.commits_readable:
        print(
            f"::warning title=dependabot-automerge::could not read the commits "
            f"of {pr.owner}/{pr.repo}#{pr.number}, so the commit-authorship "
            f"check did not run and eligibility rests on the pull request's "
            f"author alone. A change pushed onto this branch by someone other "
            f"than Dependabot would not be detected."
        )


def _commit_audit_outcome(pr: PullRequestContext) -> str:
    """Return the commit audit's outcome as one of three fixed words.

    ``clean`` means every commit was read and every one was Dependabot's;
    ``foreign`` that at least one was not; ``unreadable`` that the check
    did not run. The value is bounded on purpose and carries no commit
    identifier, so the log line can be counted across repositories
    without becoming high-cardinality.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot the decision was taken from.

    Returns
    -------
    str
        ``clean``, ``foreign`` or ``unreadable``.
    """
    if not pr.commits_readable:
        return "unreadable"
    return "foreign" if pr.foreign_commits else "clean"


def _announce_foreign_commits(pr: PullRequestContext) -> None:
    """Name the commits that stopped the branch merging unattended."""
    named = ", ".join(str(commit) for commit in pr.foreign_commits)
    print(
        f"::notice title=dependabot-automerge::{pr.owner}/{pr.repo}#{pr.number} "
        f"carries {len(pr.foreign_commits)} commit(s) Dependabot did not write "
        f"({named}), so it will not merge unattended. A change pushed onto a "
        f"Dependabot branch needs its own pull request and its own review."
    )


def _commit_authors(commit: dict[str, JsonValue]) -> tuple[tuple[str, ...], bool]:
    """Return the logins credited on one commit, and whether that is all.

    Parameters
    ----------
    commit : dict
        The commit node from the GraphQL query.

    Returns
    -------
    tuple[tuple[str, ...], bool]
        The credited logins, and whether the connection returned every
        one. ``totalCount`` above the number of nodes means the credit
        list was cut off at the page size.
    """
    authors = commit.get("authors")
    if not isinstance(authors, dict):
        return (), True
    nodes = authors.get("nodes")
    if not isinstance(nodes, list):
        return (), True
    logins: list[str] = []
    for node in nodes:
        user = node.get("user") if isinstance(node, dict) else None
        login = user.get("login") if isinstance(user, dict) else None
        logins.append(login if isinstance(login, str) and login else UNKNOWN_AUTHOR)
    total = authors.get("totalCount")
    complete = not isinstance(total, int) or total <= len(logins)
    return tuple(logins), complete


def _commit_page(pull_request: dict[str, JsonValue]) -> CommitPage | None:
    """Translate one page of the GraphQL commit connection.

    This is the whole of the GitHub shape's reach into the eligibility
    rule: everything past it reads :class:`CommitRecord`. Keeping the two
    apart is what lets the rule be stated and exercised without a GraphQL
    response, and what stops a schema change from quietly meaning a
    different rule.

    Parameters
    ----------
    pull_request : dict
        The pull request node from the GraphQL query.

    Returns
    -------
    CommitPage or None
        The commits on this page and the cursor for the next, or None
        when the response carried no commit list at all.
    """
    commits = pull_request.get("commits")
    nodes = commits.get("nodes") if isinstance(commits, dict) else None
    if not isinstance(commits, dict) or not isinstance(nodes, list):
        return None
    records: list[CommitRecord] = []
    for node in nodes:
        commit = node.get("commit") if isinstance(node, dict) else None
        if not isinstance(commit, dict):
            continue
        oid = commit.get("oid")
        authors, complete = _commit_authors(commit)
        records.append(
            CommitRecord(
                oid=oid if isinstance(oid, str) else UNKNOWN_AUTHOR,
                authors=authors,
                authors_complete=complete,
            )
        )
    return CommitPage(records=tuple(records), next_cursor=_next_cursor(commits))


def _next_cursor(commits: dict[str, JsonValue]) -> str | None:
    """Return the cursor for the next commit page, or None at the end.

    Parameters
    ----------
    commits : dict
        The commit connection from the GraphQL query.

    Returns
    -------
    str or None
        The end cursor when another page follows.
    """
    page_info = commits.get("pageInfo")
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    return cursor if isinstance(cursor, str) and cursor else None


def _foreign_commits(records: typ.Sequence[CommitRecord]) -> tuple[ForeignCommit, ...]:
    """Apply the eligibility rule to commits already in domain terms.

    The rule: every login credited on every commit must be Dependabot's,
    and the whole credit list must have been read. A commit whose authors
    came back truncated is reported foreign rather than waved through,
    because the check exists to certify the branch and a partial list
    certifies nothing. That is the opposite of the unreadable-list case
    in :func:`_audit_commits`, and deliberately so: there the branch's
    commits could not be seen at all, which is a query fault affecting
    every consumer at once; here one visible commit could not be read to
    the end, which is a property of that commit.

    Parameters
    ----------
    records : typ.Sequence[CommitRecord]
        The branch's commits.

    Returns
    -------
    tuple[ForeignCommit, ...]
        One entry per commit that fails the rule, in branch order.
    """
    foreign: list[ForeignCommit] = []
    for record in records:
        outside = [name for name in record.authors if name not in DEPENDABOT_LOGINS]
        if not outside:
            if record.authors_complete:
                continue
            foreign.append(ForeignCommit(oid=record.oid, author=UNREAD_CO_AUTHOR))
            continue
        foreign.append(ForeignCommit(oid=record.oid, author=outside[0]))
    return tuple(foreign)


def _audit_commits(pull_request: dict[str, JsonValue]) -> CommitAudit:
    """Find the commits on one page that Dependabot did not write.

    Callers that must cover a whole branch use :func:`_fetch_pull_request`,
    which pages the connection first. This composes the adapter and the
    rule over a single response.

    Parameters
    ----------
    pull_request : dict
        The pull request node from the GraphQL query.

    Returns
    -------
    CommitAudit
        Whether the commit list could be read, and one entry per commit
        with an author outside :data:`DEPENDABOT_LOGINS`. An unreadable
        list yields no foreign commits, so the check fails open: a query
        change that stopped returning commits would otherwise halt every
        consumer's automerge at once, which is a worse failure than the
        one this prevents. ``readable`` is what makes that loss visible
        rather than silent.
    """
    page = _commit_page(pull_request)
    if page is None:
        return CommitAudit(readable=False, foreign=())
    return CommitAudit(readable=True, foreign=_foreign_commits(page.records))


def _extract_author_login(pull_request: dict[str, JsonValue]) -> str:
    """Extract author login from PR data, returning empty string if unavailable."""
    author_obj = pull_request.get("author")
    if not isinstance(author_obj, dict):
        return ""
    login = author_obj.get("login")
    if not isinstance(login, str):
        return ""
    return login


def _extract_labels(pull_request: dict[str, JsonValue]) -> tuple[str, ...]:
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


def _extract_merge_state_status(
    pull_request: dict[str, JsonValue],
) -> MergeStateStatus:
    """Extract mergeStateStatus from PR data, normalized."""
    return _extract_enum(
        pull_request,
        "mergeStateStatus",
        MergeStateStatus,
        MergeStateStatus.UNKNOWN,
    )


def _extract_mergeable_state(pull_request: dict[str, JsonValue]) -> MergeableState:
    """Extract mergeable state from PR data, normalized."""
    return _extract_enum(
        pull_request,
        "mergeable",
        MergeableState,
        MergeableState.UNKNOWN,
    )


def _commit_variables(
    owner: str, repo: str, number: int, cursor: str | None
) -> dict[str, JsonValue]:
    """Build the variables both commit-bearing queries take.

    Parameters
    ----------
    owner : str
        The repository owner.
    repo : str
        The repository name.
    number : int
        The pull request number.
    cursor : str or None
        Where to resume the commit connection, or None for the first page.

    Returns
    -------
    dict
        The GraphQL variables.
    """
    return {
        "owner": owner,
        "name": repo,
        "number": number,
        "commitPageSize": COMMIT_PAGE_SIZE,
        "authorPageSize": AUTHOR_PAGE_SIZE,
        "commitCursor": cursor,
    }


def _pull_request_node(
    data: dict[str, JsonValue], owner: str, repo: str, number: int
) -> dict[str, JsonValue]:
    """Unwrap the pull request node, failing when it is absent.

    Parameters
    ----------
    data : dict
        A GraphQL response body.
    owner : str
        The repository owner.
    repo : str
        The repository name.
    number : int
        The pull request number.

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
    return fail(f"Pull request {owner}/{repo}#{number} was not found.")


def _audit_whole_branch(
    token: str,
    owner: str,
    repo: str,
    number: int,
    first_page: dict[str, JsonValue],
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
    owner : str
        The repository owner.
    repo : str
        The repository name.
    number : int
        The pull request number.
    first_page : dict
        The pull request node already fetched, carrying page one.

    Returns
    -------
    CommitAudit
        The audit over every commit, or an unreadable result when a page
        carried no commit list or the branch exceeded
        :data:`MAX_COMMIT_PAGES`.
    """
    page = _commit_page(first_page)
    if page is None:
        return CommitAudit(readable=False, foreign=())
    records = list(page.records)
    cursor = page.next_cursor
    pages = 1
    while cursor is not None:
        if pages >= MAX_COMMIT_PAGES:
            return CommitAudit(readable=False, foreign=())
        data = request_graphql(
            token,
            COMMITS_PAGE_QUERY,
            _commit_variables(owner, repo, number, cursor),
        )
        node = _pull_request_node(data, owner, repo, number)
        page = _commit_page(node)
        if page is None:
            return CommitAudit(readable=False, foreign=())
        records.extend(page.records)
        cursor = page.next_cursor
        pages += 1
    return CommitAudit(readable=True, foreign=_foreign_commits(records))


def _fetch_pull_request(
    token: str, owner: str, repo: str, number: int
) -> PullRequestContext:
    """Fetch PR metadata from the GitHub GraphQL API."""
    data = request_graphql(
        token,
        PULL_REQUEST_QUERY,
        _commit_variables(owner, repo, number, None),
    )
    pull_request = _pull_request_node(data, owner, repo, number)
    author_login = _extract_author_login(pull_request)
    labels = _extract_labels(pull_request)
    audit = _audit_whole_branch(token, owner, repo, number, pull_request)
    auto_merge_enabled = pull_request.get("autoMergeRequest") is not None
    node_id = pull_request.get("id")
    return PullRequestContext(
        number=number,
        owner=owner,
        repo=repo,
        author=author_login,
        is_draft=bool(pull_request.get("isDraft", False)),
        labels=labels,
        node_id=node_id if isinstance(node_id, str) else None,
        auto_merge_enabled=auto_merge_enabled,
        merge_state_status=_extract_merge_state_status(pull_request),
        mergeable_state=_extract_mergeable_state(pull_request),
        foreign_commits=audit.foreign,
        commits_readable=audit.readable,
    )


def _classify_merge_state(
    merge_state: MergeStateStatus, mergeable_state: MergeableState
) -> tuple[MergeStateClassification, str | None]:
    """Classify merge state as ok, merge, skip, or retry with a reason.

    ``ok`` means auto-merge can be armed (notably ``BLOCKED``, where required
    checks are still pending). ``merge`` means the PR is already mergeable, so
    it must be merged directly because GitHub rejects
    ``enablePullRequestAutoMerge`` on an already-mergeable pull request.
    """
    if mergeable_state in MERGEABLE_SKIP_REASONS:
        return "skip", MERGEABLE_SKIP_REASONS[mergeable_state]
    if merge_state in MERGE_STATE_SKIP_REASONS:
        return "skip", MERGE_STATE_SKIP_REASONS[merge_state]
    if merge_state in MERGE_STATE_DIRECT_MERGE:
        return "merge", "already-mergeable"
    if merge_state in MERGE_STATE_RETRYABLE or mergeable_state in MERGEABLE_RETRYABLE:
        return "retry", "merge-state-unknown"
    return "ok", None


def _parse_env_int(name: str, default: int) -> int:
    """Parse an integer from the environment, or return the default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        fail(f"Invalid value for {name}: {value!r}. Expected an integer.")
    if parsed < 0:
        fail(f"Invalid value for {name}: {value!r}. Expected a non-negative integer.")
    return parsed


def _parse_env_float(name: str, default: float) -> float:
    """Parse a float from the environment, or return the default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        fail(f"Invalid value for {name}: {value!r}. Expected a number.")
    if not math.isfinite(parsed):
        fail(f"Invalid value for {name}: {value!r}. Expected a finite number.")
    if parsed < 0:
        fail(f"Invalid value for {name}: {value!r}. Expected a non-negative number.")
    return parsed


@dataclasses.dataclass(frozen=True, slots=True)
class MergeStateRetryConfig:
    """Configuration for merge state refresh retry behaviour."""

    max_attempts: int
    base_sleep: float
    max_sleep: float


def _merge_state_retry_config() -> MergeStateRetryConfig:
    """Return retry configuration for merge state refresh."""
    max_attempts = _parse_env_int(
        MERGE_STATE_MAX_ATTEMPTS_ENV, MERGE_STATE_MAX_ATTEMPTS_DEFAULT
    )
    base_sleep = _parse_env_float(
        MERGE_STATE_BASE_SLEEP_ENV, MERGE_STATE_BASE_SLEEP_DEFAULT
    )
    max_sleep = _parse_env_float(
        MERGE_STATE_MAX_SLEEP_ENV, MERGE_STATE_MAX_SLEEP_DEFAULT
    )
    return MergeStateRetryConfig(
        max_attempts=max_attempts,
        base_sleep=base_sleep,
        max_sleep=max_sleep,
    )


def _refresh_merge_state(
    token: str,
    pr: PullRequestContext,
    *,
    config: MergeStateRetryConfig | None = None,
) -> PullRequestContext:
    """Refresh PR merge state, retrying while mergeability is unknown."""
    current = pr
    retry_config = config if config is not None else _merge_state_retry_config()
    for attempt in range(retry_config.max_attempts):
        state, _reason = _classify_merge_state(
            current.merge_state_status,
            current.mergeable_state,
        )
        if state != "retry":
            return current
        sleep_seconds = min(
            retry_config.base_sleep * (2**attempt),
            retry_config.max_sleep,
        )
        time.sleep(sleep_seconds)
        current = _fetch_pull_request(
            token, current.owner, current.repo, current.number
        )
    return current


def _enable_automerge(token: str, pull_request_id: str, merge_method: str) -> None:
    """Enable auto-merge on a pull request via the GitHub GraphQL API."""
    request_graphql(
        token,
        ENABLE_AUTOMERGE_MUTATION,
        {"pullRequestId": pull_request_id, "mergeMethod": merge_method},
    )


def _disable_automerge(token: str, pull_request_id: str) -> None:
    """Cancel an auto-merge request via the GitHub GraphQL API."""
    request_graphql(
        token,
        DISABLE_AUTOMERGE_MUTATION,
        {"pullRequestId": pull_request_id},
    )


def _merge_pull_request(token: str, pull_request_id: str, merge_method: str) -> None:
    """Merge a pull request directly via the GitHub GraphQL API."""
    request_graphql(
        token,
        MERGE_PULL_REQUEST_MUTATION,
        {"pullRequestId": pull_request_id, "mergeMethod": merge_method},
    )


def _handle_dry_run(
    event: dict[str, JsonValue] | None,
    repo_full_name: str,
    *,
    config: AutomergeConfig,
) -> None:
    """Handle the dry-run execution path without API calls."""
    if event is None:
        fail("Dry-run mode requires GITHUB_EVENT_PATH with pull_request data.")
    snapshot = _snapshot_from_event(event, repo_full_name)
    decision = _evaluate(snapshot, config.required_label)
    _emit_decision(
        snapshot,
        decision,
        config=config,
    )


def _stop_unless_eligible(
    github_token: str,
    pr: PullRequestContext,
    *,
    config: AutomergeConfig,
) -> bool:
    """Report the decision and stop when the pull request is not eligible.

    Cancels an auto-merge request that was armed before the branch went
    foreign. Declining to arm one is not enough on its own: GitHub keeps
    an existing request alive across a push, so a request armed while the
    branch was Dependabot's would still merge the commit that made it
    foreign as soon as the required checks passed. That is the outcome
    this whole check exists to prevent, so the request is withdrawn.

    Parameters
    ----------
    github_token : str
        A GitHub token.
    pr : PullRequestContext
        The snapshot to judge.
    config : AutomergeConfig
        The run's configuration.

    Returns
    -------
    bool
        True when the caller must stop.
    """
    decision = _evaluate(pr, config.required_label)
    if decision.status == "ready":
        return False
    if pr.foreign_commits and pr.auto_merge_enabled and pr.node_id:
        _disable_automerge(github_token, pr.node_id)
        print(
            f"::notice title=dependabot-automerge::cancelled the auto-merge "
            f"request armed on {pr.owner}/{pr.repo}#{pr.number} before the "
            f"branch gained a commit Dependabot did not write; it would "
            f"otherwise have merged that commit once the required checks "
            f"passed."
        )
        decision = Decision(
            status="cancelled",
            reason=decision.reason,
        )
    _emit_decision(pr, decision, config=config)
    return True


def _handle_live_execution(
    github_token: str,
    context: RuntimeContext,
    *,
    config: AutomergeConfig,
) -> None:
    """Handle the live execution path that talks to the GitHub API."""
    owner, repo = _split_repo(context.repo_full_name)
    pr_number = _resolve_pull_request_number(
        context.pull_request_number,
        context.event,
    )

    pr = _fetch_pull_request(github_token, owner, repo, pr_number)
    if _stop_unless_eligible(github_token, pr, config=config):
        return

    if pr.auto_merge_enabled:
        _emit_decision(
            pr,
            Decision(status="enabled", reason="already-enabled"),
            config=config,
        )
        return

    pr = _refresh_merge_state(github_token, pr)
    # The refresh refetched the pull request, so it also refetched who
    # wrote the commits. A push landing inside the retry window is
    # visible in this snapshot and nowhere else, and arming auto-merge on
    # the strength of the earlier one would merge it unreviewed.
    if _stop_unless_eligible(github_token, pr, config=config):
        return
    if pr.auto_merge_enabled:
        _emit_decision(
            pr,
            Decision(status="enabled", reason="already-enabled"),
            config=config,
        )
        return

    state, reason = _classify_merge_state(
        pr.merge_state_status,
        pr.mergeable_state,
    )
    if state == "skip":
        _emit_decision(
            pr,
            Decision(status="skipped", reason=reason or "merge-state-unknown"),
            config=config,
        )
        return
    if state == "retry":
        _emit_decision(
            pr,
            Decision(status="skipped", reason="merge-state-unknown"),
            config=config,
        )
        return

    if not pr.node_id:
        fail("Pull request node ID missing from GitHub response.")

    if state == "merge":
        _merge_pull_request(github_token, pr.node_id, config.merge_method)
        _emit_decision(
            pr,
            Decision(status="merged", reason="merged-directly"),
            config=config,
        )
        return

    _enable_automerge(github_token, pr.node_id, config.merge_method)
    _emit_decision(
        pr,
        Decision(status="enabled", reason="enabled"),
        config=config,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class AutomergeOptions:
    """CLI options for automerge execution.

    Attributes
    ----------
    merge_method : str
        Merge method to use: ``squash``, ``merge``, or ``rebase``.
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.
    dry_run : bool
        If True, logs the decision without calling the GitHub API.
    pull_request_number : int or None
        Explicit PR number override, or None to resolve from event payload.
    repository : str or None
        Repository override in ``owner/repo`` form, or None to resolve from
        event payload or environment.
    """

    merge_method: typ.Annotated[
        str,
        Parameter(
            help="Merge method to use (squash, merge, rebase).",
            env_var="INPUT_MERGE_METHOD",
        ),
    ] = "squash"
    required_label: typ.Annotated[
        str | None,
        Parameter(
            help="Required label on the pull request.",
            env_var="INPUT_REQUIRED_LABEL",
        ),
    ] = "dependencies"
    dry_run: typ.Annotated[
        bool,
        Parameter(
            help="Emit decision output without API calls.",
            env_var="INPUT_DRY_RUN",
        ),
    ] = False
    pull_request_number: typ.Annotated[
        int | None,
        Parameter(
            help="Pull request number override.",
            env_var="INPUT_PULL_REQUEST_NUMBER",
        ),
    ] = None
    repository: typ.Annotated[
        str | None,
        Parameter(
            help="Repository override in owner/repo form.",
            env_var="INPUT_REPOSITORY",
        ),
    ] = None


DEFAULT_AUTOMERGE_OPTIONS = AutomergeOptions()


@app.default
def main(
    *,
    github_token: typ.Annotated[
        str, Parameter(required=True, env_var="INPUT_GITHUB_TOKEN")
    ],
    options: AutomergeOptions = DEFAULT_AUTOMERGE_OPTIONS,
) -> None:
    """Evaluate a PR and enable auto-merge when policy allows.

    This is the CLI entrypoint. It loads configuration from environment
    variables and CLI arguments, evaluates the pull request against
    eligibility rules, and enables auto-merge if all criteria are met.

    Parameters
    ----------
    github_token : str
        GitHub token with ``contents:write`` and ``pull-requests:write``
        permissions. Read from ``INPUT_GITHUB_TOKEN`` environment variable.
    options : AutomergeOptions
        Configuration options including merge method, required label,
        dry-run mode, and optional repository/PR number overrides.

    Raises
    ------
    SystemExit
        Exits with code 1 on validation errors, missing configuration,
        or GitHub API failures. Error details are logged to stderr.
    """
    normalized_label = _normalize_label(options.required_label)
    normalized_merge_method = _normalize_merge_method(options.merge_method)
    config = AutomergeConfig(
        merge_method=normalized_merge_method,
        required_label=normalized_label,
        dry_run=options.dry_run,
    )

    event = _load_event()
    repo_full_name = _resolve_repository(options.repository, event)

    if options.dry_run:
        _handle_dry_run(
            event,
            repo_full_name,
            config=config,
        )
        return
    context = RuntimeContext(
        repo_full_name=repo_full_name,
        event=event,
        pull_request_number=options.pull_request_number,
    )
    _handle_live_execution(
        github_token,
        context,
        config=config,
    )


if __name__ == "__main__":
    app()
