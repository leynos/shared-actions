"""Unit tests for the dependabot auto-merge helper script."""

from __future__ import annotations

import json
import typing as typ

import pytest

from workflow_scripts import dependabot_automerge

if typ.TYPE_CHECKING:
    from pathlib import Path

TEST_TOKEN = "test-token"  # noqa: S105


def _write_event(tmp_path: Path, payload: dict[str, object]) -> Path:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    return event_path


def test_dry_run_skips_non_dependabot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-Dependabot authors are skipped in dry-run mode."""
    event = {
        "pull_request": {
            "number": 7,
            "draft": False,
            "user": {"login": "someone"},
            "labels": [{"name": "dependencies"}],
        },
        "repository": {"full_name": "acme/example"},
    }
    event_path = _write_event(tmp_path, event)

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        dry_run=True,
        required_label="dependencies",
    )

    captured = capsys.readouterr()
    assert "automerge_status=skipped" in captured.out
    assert "automerge_reason=author-not-dependabot" in captured.out


def test_dry_run_requires_event_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run mode errors without a pull_request event payload."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/example")

    with pytest.raises(SystemExit):
        dependabot_automerge.main(
            github_token=TEST_TOKEN,
            dry_run=True,
            required_label="dependencies",
        )

    captured = capsys.readouterr()
    assert "automerge_status=error" in captured.err
    assert "Dry-run mode requires GITHUB_EVENT_PATH" in captured.err


def test_dry_run_eligible_dependabot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Eligible Dependabot PRs log dry-run readiness."""
    event = {
        "pull_request": {
            "number": 9,
            "draft": False,
            "user": {"login": "dependabot[bot]"},
            "labels": [{"name": "dependencies"}],
        },
        "repository": {"full_name": "acme/example"},
    }
    event_path = _write_event(tmp_path, event)

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        dry_run=True,
        required_label="dependencies",
    )

    captured = capsys.readouterr()
    assert "automerge_status=dry-run" in captured.out
    assert "automerge_reason=eligible" in captured.out


def test_enables_automerge_when_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Eligible PRs enable auto-merge via the GitHub API."""

    def fake_request(
        token: str, query: str, variables: dict[str, object]
    ) -> dict[str, object]:
        if "enablePullRequestAutoMerge" in query:
            return {"enablePullRequestAutoMerge": {"pullRequest": {"number": 12}}}
        return {
            "repository": {
                "pullRequest": {
                    "id": "PR_12",
                    "number": 12,
                    "isDraft": False,
                    "author": {"login": "dependabot[bot]"},
                    "labels": {"nodes": [{"name": "dependencies"}]},
                    "autoMergeRequest": None,
                }
            }
        }

    monkeypatch.setattr(dependabot_automerge, "_request_graphql", fake_request)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        repository="acme/example",
        pull_request_number=12,
    )

    captured = capsys.readouterr()
    assert "automerge_status=enabled" in captured.out
    assert "automerge_reason=enabled" in captured.out
