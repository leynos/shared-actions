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

- The PR author is ``dependabot[bot]``
- The PR is not a draft
- The required label (default: ``dependencies``) is present

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
auto-merge on the target pull request. This modifies the PR's auto-merge state.

See Also
--------
main : The CLI entrypoint function.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
import typing as typ
from pathlib import Path

import httpx
from cyclopts import App, Parameter

DEPENDABOT_LOGIN = "dependabot[bot]"
GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

MERGE_METHODS = {
    "merge": "MERGE",
    "rebase": "REBASE",
    "squash": "SQUASH",
}

PULL_REQUEST_QUERY = """
query PullRequestInfo($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      number
      isDraft
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
    }
  }
}
"""

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

app = App()


@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Snapshot of the pull request metadata used for gating.

    Attributes
    ----------
    number : int
        The pull request number.
    owner : str
        The repository owner (organisation or user).
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
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None = None
    auto_merge_enabled: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """Decision describing whether auto-merge should proceed.

    Attributes
    ----------
    status : str
        The decision status: ``skipped``, ``ready``, ``enabled``, or ``error``.
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
        The normalised merge method (``SQUASH``, ``MERGE``, or ``REBASE``).
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
    event: dict[str, typ.Any] | None
    pull_request_number: int | None


def _log_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _emit(key: str, value: object, *, stream: typ.TextIO | None = None) -> None:
    target = stream if stream is not None else sys.stdout
    print(f"{key}={_log_value(value)}", file=target)


def _fail(message: str) -> typ.NoReturn:
    _emit("automerge_status", "error", stream=sys.stderr)
    _emit("automerge_error", message, stream=sys.stderr)
    raise SystemExit(1)


def _load_event() -> dict[str, typ.Any] | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"Failed to parse event payload: {exc}")


def _normalize_label(required_label: str | None) -> str | None:
    if required_label is None:
        return None
    label = required_label.strip()
    return label or None


def _normalize_merge_method(merge_method: str) -> str:
    normalized = merge_method.strip().lower()
    if normalized not in MERGE_METHODS:
        allowed = ", ".join(sorted(MERGE_METHODS))
        _fail(f"Invalid merge_method '{merge_method}'. Allowed: {allowed}.")
    return MERGE_METHODS[normalized]


def _get_repo_from_event(event: dict[str, typ.Any] | None) -> str:
    """Return the repository full name from an event payload when available."""
    if not event:
        return ""
    repo = event.get("repository", {}).get("full_name")
    if isinstance(repo, str):
        return repo.strip()
    return ""


def _resolve_repository(
    repository: str | None, event: dict[str, typ.Any] | None
) -> str:
    if repository and (candidate := repository.strip()):
        return candidate
    if repo := _get_repo_from_event(event):
        return repo
    if repo := os.environ.get("GITHUB_REPOSITORY"):
        return repo
    _fail("Repository not provided. Set INPUT_REPOSITORY or GITHUB_REPOSITORY.")


def _split_repo(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        _fail(f"Repository '{full_name}' must be in owner/repo form.")
    return parts[0], parts[1]


def _resolve_pull_request_number(
    pull_request_number: int | None, event: dict[str, typ.Any] | None
) -> int:
    if pull_request_number is not None:
        return pull_request_number
    if event and (number := event.get("pull_request", {}).get("number")) is not None:
        try:
            return int(number)
        except (TypeError, ValueError):
            pass
    _fail(
        "Pull request number not provided. Set INPUT_PULL_REQUEST_NUMBER or include "
        "it in the event payload."
    )


def _parse_pr_number(pr: dict[str, typ.Any]) -> int:
    """Parse and validate the pull request number from event payload data."""
    number = pr.get("number")
    if number is None:
        _fail("Event payload missing pull_request.number.")
    try:
        return int(number)
    except (TypeError, ValueError):
        _fail("Event payload pull_request.number is not an integer.")


def _labels_from_pr(pr: dict[str, typ.Any]) -> tuple[str, ...]:
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
    event: dict[str, typ.Any], repo_full_name: str
) -> PullRequestContext:
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        _fail("Event payload does not include pull_request data.")
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
    if pr.author != DEPENDABOT_LOGIN:
        return Decision(status="skipped", reason="author-not-dependabot")
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
    if decision.status == "ready" and config.dry_run:
        status = "dry-run"
        reason = decision.reason
    else:
        status = decision.status
        reason = decision.reason
    _emit("automerge_status", status)
    _emit("automerge_reason", reason)
    _emit("automerge_merge_method", config.merge_method)
    _emit("automerge_required_label", config.required_label or "")
    _emit("automerge_repository", f"{pr.owner}/{pr.repo}")
    _emit("automerge_pr_number", pr.number)
    _emit("automerge_author", pr.author)
    _emit("automerge_draft", str(pr.is_draft).lower())
    _emit("automerge_labels", pr.labels)


def _should_retry(attempt: int, max_retries: int) -> bool:
    return attempt < max_retries


def _backoff_sleep(attempt: int, base_seconds: float = 1.0) -> None:
    time.sleep(base_seconds * (2**attempt))


def _parse_graphql_response(response: httpx.Response) -> dict[str, typ.Any]:
    """Parse and validate a GraphQL response, returning the data payload."""
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        _fail(f"GitHub API response was not valid JSON: {exc}")

    errors = payload.get("errors")
    if errors:
        _fail(f"GitHub API GraphQL errors: {errors}")

    data = payload.get("data")
    if data is None:
        _fail("GitHub API returned no data.")
    return data


def _execute_graphql_attempt(
    token: str, query: str, variables: dict[str, typ.Any]
) -> httpx.Response | None:
    """Execute a single GraphQL request attempt, returning None on connection error."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        with httpx.Client(timeout=30) as client:
            return client.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                headers=headers,
            )
    except httpx.HTTPError:
        return None


def _handle_response_or_retry(
    response: httpx.Response | None, attempt: int, max_retries: int
) -> httpx.Response | None:
    """Process response or decide to retry. Returns None to signal retry."""
    if response is None:
        if _should_retry(attempt, max_retries):
            _backoff_sleep(attempt)
            return None
        _fail("GitHub API request failed after retries: connection error")

    if 400 <= response.status_code < 500:
        _fail(f"GitHub API error {response.status_code}: {response.text}")

    if response.status_code >= 500:
        if _should_retry(attempt, max_retries):
            _backoff_sleep(attempt)
            return None
        msg = f"GitHub API error {response.status_code} after retries"
        _fail(f"{msg}: {response.text}")

    return response


def _request_graphql(
    token: str, query: str, variables: dict[str, typ.Any]
) -> dict[str, typ.Any]:
    max_retries = 3

    for attempt in range(max_retries + 1):
        response = _execute_graphql_attempt(token, query, variables)
        processed_response = _handle_response_or_retry(response, attempt, max_retries)
        if processed_response is None:
            continue
        return _parse_graphql_response(processed_response)

    _fail("GitHub API request failed unexpectedly.")


def _fetch_pull_request(
    token: str, owner: str, repo: str, number: int
) -> PullRequestContext:
    data = _request_graphql(
        token,
        PULL_REQUEST_QUERY,
        {"owner": owner, "name": repo, "number": number},
    )
    pull_request = data.get("repository", {}).get("pullRequest")
    if pull_request is None:
        _fail(f"Pull request {owner}/{repo}#{number} was not found.")
    author = pull_request.get("author", {}).get("login")
    author_login = author if isinstance(author, str) else ""
    labels = [
        node.get("name")
        for node in pull_request.get("labels", {}).get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    ]
    auto_merge_enabled = pull_request.get("autoMergeRequest") is not None
    return PullRequestContext(
        number=number,
        owner=owner,
        repo=repo,
        author=author_login,
        is_draft=bool(pull_request.get("isDraft", False)),
        labels=tuple(labels),
        node_id=pull_request.get("id"),
        auto_merge_enabled=auto_merge_enabled,
    )


def _enable_automerge(token: str, pull_request_id: str, merge_method: str) -> None:
    _request_graphql(
        token,
        ENABLE_AUTOMERGE_MUTATION,
        {"pullRequestId": pull_request_id, "mergeMethod": merge_method},
    )


def _handle_dry_run(
    event: dict[str, typ.Any] | None,
    repo_full_name: str,
    *,
    config: AutomergeConfig,
) -> None:
    """Handle the dry-run execution path without API calls."""
    if event is None:
        _fail("Dry-run mode requires GITHUB_EVENT_PATH with pull_request data.")
    snapshot = _snapshot_from_event(event, repo_full_name)
    decision = _evaluate(snapshot, config.required_label)
    _emit_decision(
        snapshot,
        decision,
        config=config,
    )


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
    decision = _evaluate(pr, config.required_label)
    if decision.status != "ready":
        _emit_decision(
            pr,
            decision,
            config=config,
        )
        return

    if pr.auto_merge_enabled:
        _emit_decision(
            pr,
            Decision(status="enabled", reason="already-enabled"),
            config=config,
        )
        return

    if not pr.node_id:
        _fail("Pull request node ID missing from GitHub response.")

    _enable_automerge(github_token, pr.node_id, config.merge_method)
    _emit_decision(
        pr,
        Decision(status="enabled", reason="enabled"),
        config=config,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class AutomergeOptions:
    """CLI options for automerge execution."""

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
