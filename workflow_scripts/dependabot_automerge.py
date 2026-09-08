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
- Every commit on the branch is Dependabot's, and the whole branch was
  read

Commit Audit
------------
The author field names who opened the pull request, not who wrote what
is on the branch, so a maintainer pushing to a Dependabot branch would
otherwise be merged unattended. Every commit is therefore audited.

The commit connection is paged to the end rather than read once: a
connection read to its page size looks complete, and a foreign commit
past that limit would be certified as Dependabot's. A branch longer
than the page cap, a page carrying no commit list, or an author list
the connection could not account for is reported unreadable rather than
clean, because the check exists to certify the branch and a partial
read certifies nothing.

A foreign commit does more than withhold a merge. Auto-merge already
armed on the pull request is withdrawn, since arming it was a decision
taken while the branch was still Dependabot's and GitHub keeps such a
request alive across a push. The withdrawal is deliberately limited to
that case: a branch skipped for any other reason, an unreadable audit
among them, keeps its request, because cancelling there would undo the
arming this workflow exists to do. Both mutations are bound to the head
commit the audit read, so a push racing the run cannot have the decision
applied to it.

The rule itself lives in ``dependabot_commit_audit`` and takes values
rather than a client, so it can be exercised without a network. The
GitHub boundary and the branch audit live in ``dependabot_github``,
whose readers take the GraphQL call as an argument that defaults to the
live client.

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
import json
import os
import time
import typing as typ
from pathlib import Path

from cyclopts import App, Parameter

if __package__:
    from .dependabot_commit_audit import (
        CommitAudit,
        CommitRecord,
        DependabotLogin,
        ForeignCommit,
    )
    from .dependabot_decision import (
        AutomergeConfig,
        Decision,
        PullRequestContext,
        armed_request_to_withdraw,
        evaluate,
    )
    from .dependabot_github import (
        GraphQLQuery,
        PullRequestRef,
        fetch_pull_request,
    )
    from .dependabot_merge_state import (
        MergeStateRetryConfig,
        classify_merge_state,
        merge_state_retry_config,
    )
    from .dependabot_queries import (
        DISABLE_AUTOMERGE_MUTATION,
        ENABLE_AUTOMERGE_MUTATION,
        MERGE_PULL_REQUEST_MUTATION,
    )
    from .dependabot_report import emit_decision
    from .graphql_client import JsonValue, request_graphql
    from .output import fail
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        CommitAudit,
        CommitRecord,
        DependabotLogin,
        ForeignCommit,
    )
    from dependabot_decision import (  # type: ignore[import-not-found,no-redef]
        AutomergeConfig,
        Decision,
        PullRequestContext,
        armed_request_to_withdraw,
        evaluate,
    )
    from dependabot_github import (  # type: ignore[import-not-found,no-redef]
        GraphQLQuery,
        PullRequestRef,
        fetch_pull_request,
    )
    from dependabot_merge_state import (  # type: ignore[import-not-found,no-redef]
        MergeStateRetryConfig,
        classify_merge_state,
        merge_state_retry_config,
    )
    from dependabot_queries import (  # type: ignore[import-not-found,no-redef]
        DISABLE_AUTOMERGE_MUTATION,
        ENABLE_AUTOMERGE_MUTATION,
        MERGE_PULL_REQUEST_MUTATION,
    )
    from dependabot_report import (  # type: ignore[import-not-found,no-redef]
        emit_decision,
    )
    from graphql_client import (  # type: ignore[import-not-found,no-redef]
        JsonValue,
        request_graphql,
    )
    from output import fail  # type: ignore[import-not-found,no-redef]


def _fetch_pull_request(
    token: str,
    ref: PullRequestRef,
    *,
    query: GraphQLQuery | None = None,
) -> PullRequestContext:
    """Read one pull request through the live client by default.

    The adapter in ``dependabot_github`` requires the call rather than
    naming one, so this module is where the live client is chosen. It is
    read at call time, so patching this module's ``request_graphql``
    still redirects the read path.

    Parameters
    ----------
    token : str
        A GitHub token.
    ref : PullRequestRef
        Where the pull request lives.
    query : GraphQLQuery or None
        A call to use instead of the live client.

    Returns
    -------
    PullRequestContext
        The pull request as the decision layer reads it.
    """
    return fetch_pull_request(token, ref, query=query or request_graphql)


#: Re-exported so the workflow module remains the single import for
#: callers and tests that do not care where the audit lives.
__all__ = [
    "CommitAudit",
    "CommitRecord",
    "DependabotLogin",
    "ForeignCommit",
    "main",
]


MERGE_METHODS = {
    "merge": "MERGE",
    "rebase": "REBASE",
    "squash": "SQUASH",
}

app = App()


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
    """Build a PullRequestContext from a GitHub event payload.

    The event carries no commit list, so the audit did not run here and
    the context says so. Leaving ``commits_readable`` at its default
    would have the dry run report ``automerge_commit_audit=clean``, which
    claims a check that was never made and does so in the one output
    meant to be counted.
    """
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
        commits_readable=False,
    )


def _refresh_merge_state(
    token: str,
    pr: PullRequestContext,
    *,
    config: MergeStateRetryConfig | None = None,
) -> PullRequestContext:
    """Refresh PR merge state, retrying while mergeability is unknown."""
    current = pr
    retry_config = config if config is not None else merge_state_retry_config()
    for attempt in range(retry_config.max_attempts):
        state, _reason = classify_merge_state(
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
            token,
            PullRequestRef(
                owner=current.owner, repo=current.repo, number=current.number
            ),
        )
    return current


def _enable_automerge(pr: PullRequestContext, token: str, merge_method: str) -> None:
    """Enable auto-merge on the head the audit read.

    Parameters
    ----------
    pr : PullRequestContext
        The audited snapshot, carrying the node and head.
    token : str
        A GitHub token.
    merge_method : str
        The normalized merge method.
    """
    _mutate(pr, token, ENABLE_AUTOMERGE_MUTATION, merge_method)


def _disable_automerge(token: str, pull_request_id: str) -> None:
    """Cancel an auto-merge request via the GitHub GraphQL API."""
    request_graphql(
        token,
        DISABLE_AUTOMERGE_MUTATION,
        {"pullRequestId": pull_request_id},
    )


def _merge_pull_request(pr: PullRequestContext, token: str, merge_method: str) -> None:
    """Merge the head the audit read, directly.

    Parameters
    ----------
    pr : PullRequestContext
        The audited snapshot, carrying the node and head.
    token : str
        A GitHub token.
    merge_method : str
        The normalized merge method.
    """
    _mutate(pr, token, MERGE_PULL_REQUEST_MUTATION, merge_method)


def _mutate(
    pr: PullRequestContext, token: str, mutation: str, merge_method: str
) -> None:
    """Send one merge mutation, bound to the head the audit read.

    ``expectedHeadOid`` is what makes the audit binding rather than
    advisory. A push can land between reading the commits and arming or
    performing the merge, and GitHub makes no head-match check without
    it, so the request would be armed on a head nobody looked at. Where
    the head has moved GitHub refuses the mutation, which fails the run:
    no merge happens, and the push that moved the head starts a new run
    that audits it from scratch.

    Parameters
    ----------
    pr : PullRequestContext
        The audited snapshot.
    token : str
        A GitHub token.
    mutation : str
        The GraphQL document to send.
    merge_method : str
        The normalized merge method.
    """
    if not pr.node_id:
        fail("Pull request node ID missing from GitHub response.")
    if not pr.head_oid:
        fail(
            f"Pull request {pr.owner}/{pr.repo}#{pr.number} reported no head "
            f"commit, so the merge cannot be bound to the head the commit "
            f"audit read. Refusing to act on an unaudited head."
        )
    request_graphql(
        token,
        mutation,
        {
            "pullRequestId": pr.node_id,
            "mergeMethod": merge_method,
            "expectedHeadOid": pr.head_oid,
        },
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
    decision = evaluate(snapshot, config.required_label)
    emit_decision(
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
    decision = evaluate(pr, config.required_label)
    if decision.status == "ready":
        return False
    armed = armed_request_to_withdraw(pr)
    if armed is not None:
        _disable_automerge(github_token, armed)
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
    emit_decision(pr, decision, config=config)
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

    pr = _fetch_pull_request(
        github_token, PullRequestRef(owner=owner, repo=repo, number=pr_number)
    )
    if _stop_unless_eligible(github_token, pr, config=config):
        return

    if pr.auto_merge_enabled:
        emit_decision(
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
        emit_decision(
            pr,
            Decision(status="enabled", reason="already-enabled"),
            config=config,
        )
        return

    state, reason = classify_merge_state(
        pr.merge_state_status,
        pr.mergeable_state,
    )
    if state == "skip":
        emit_decision(
            pr,
            Decision(status="skipped", reason=reason or "merge-state-unknown"),
            config=config,
        )
        return
    if state == "retry":
        emit_decision(
            pr,
            Decision(status="skipped", reason="merge-state-unknown"),
            config=config,
        )
        return

    if state == "merge":
        _merge_pull_request(pr, github_token, config.merge_method)
        emit_decision(
            pr,
            Decision(status="merged", reason="merged-directly"),
            config=config,
        )
        return

    _enable_automerge(pr, github_token, config.merge_method)
    emit_decision(
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
