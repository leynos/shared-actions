#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "httpx>=0.28,<0.29"]
# ///

"""Enable GitHub auto-merge for eligible Dependabot pull requests."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import typing as typ
from pathlib import Path

import cyclopts
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

app = App(config=cyclopts.config.Env("INPUT_", command=False))


@dataclasses.dataclass(frozen=True)
class PullRequestContext:
    """Snapshot of the pull request metadata used for gating."""

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None = None
    auto_merge_enabled: bool = False


@dataclasses.dataclass(frozen=True)
class Decision:
    """Decision describing whether auto-merge should proceed."""

    status: str
    reason: str


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


def _labels_from_event(event: dict[str, typ.Any]) -> tuple[str, ...]:
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        _fail("Event payload does not include pull_request data.")
    labels = []
    for label in pr.get("labels", []) or []:
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
        labels=_labels_from_event(event),
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
    merge_method: str,
    required_label: str | None,
    dry_run: bool,
) -> None:
    if decision.status == "ready" and dry_run:
        status = "dry-run"
        reason = decision.reason
    else:
        status = decision.status
        reason = decision.reason
    _emit("automerge_status", status)
    _emit("automerge_reason", reason)
    _emit("automerge_merge_method", merge_method)
    _emit("automerge_required_label", required_label or "")
    _emit("automerge_repository", f"{pr.owner}/{pr.repo}")
    _emit("automerge_pr_number", pr.number)
    _emit("automerge_author", pr.author)
    _emit("automerge_draft", str(pr.is_draft).lower())
    _emit("automerge_labels", pr.labels)


def _request_graphql(
    token: str, query: str, variables: dict[str, typ.Any]
) -> dict[str, typ.Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        _fail(f"GitHub API request failed: {exc}")

    if response.status_code >= 400:
        _fail(f"GitHub API error {response.status_code}: {response.text}")

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
    required_label: str | None,
    merge_method: str,
) -> None:
    """Handle the dry-run execution path without API calls."""
    if event is None:
        _fail("Dry-run mode requires GITHUB_EVENT_PATH with pull_request data.")
    snapshot = _snapshot_from_event(event, repo_full_name)
    decision = _evaluate(snapshot, required_label)
    _emit_decision(
        snapshot,
        decision,
        merge_method=merge_method,
        required_label=required_label,
        dry_run=True,
    )


def _handle_live_execution(
    github_token: str,
    repo_full_name: str,
    event: dict[str, typ.Any] | None,
    *,
    required_label: str | None,
    merge_method: str,
    pull_request_number: int | None,
) -> None:
    """Handle the live execution path that talks to the GitHub API."""
    owner, repo = _split_repo(repo_full_name)
    pr_number = _resolve_pull_request_number(pull_request_number, event)

    pr = _fetch_pull_request(github_token, owner, repo, pr_number)
    decision = _evaluate(pr, required_label)
    if decision.status != "ready":
        _emit_decision(
            pr,
            decision,
            merge_method=merge_method,
            required_label=required_label,
            dry_run=False,
        )
        return

    if pr.auto_merge_enabled:
        _emit_decision(
            pr,
            Decision(status="enabled", reason="already-enabled"),
            merge_method=merge_method,
            required_label=required_label,
            dry_run=False,
        )
        return

    if not pr.node_id:
        _fail("Pull request node ID missing from GitHub response.")

    _enable_automerge(github_token, pr.node_id, merge_method)
    _emit_decision(
        pr,
        Decision(status="enabled", reason="enabled"),
        merge_method=merge_method,
        required_label=required_label,
        dry_run=False,
    )


@app.default
def main(
    *,
    github_token: typ.Annotated[str, Parameter(required=True)],
    merge_method: str = "squash",
    required_label: str | None = "dependencies",
    dry_run: bool = False,
    pull_request_number: int | None = None,
    repository: str | None = None,
) -> None:
    """Evaluate a PR and enable auto-merge when policy allows."""
    normalized_label = _normalize_label(required_label)
    normalized_merge_method = _normalize_merge_method(merge_method)

    event = _load_event()
    repo_full_name = _resolve_repository(repository, event)

    if dry_run:
        _handle_dry_run(
            event,
            repo_full_name,
            required_label=normalized_label,
            merge_method=normalized_merge_method,
        )
        return
    _handle_live_execution(
        github_token,
        repo_full_name,
        event,
        required_label=normalized_label,
        merge_method=normalized_merge_method,
        pull_request_number=pull_request_number,
    )


if __name__ == "__main__":
    app()
