"""Unit tests for the dependabot auto-merge helper script."""

from __future__ import annotations

import dataclasses
import json
import typing as typ

import httpx
import pytest

from workflow_scripts import dependabot_automerge

if typ.TYPE_CHECKING:
    from pathlib import Path

# Test-only constant (not a real credential)
TEST_TOKEN = "test-token"  # noqa: S105


@dataclasses.dataclass(frozen=True, slots=True)
class DryRunTestCase:
    """Test case parameters for dry-run skip scenarios."""

    event_data: dict[str, object]
    expected_reason: str
    test_id: str


def _write_event(tmp_path: Path, payload: dict[str, object]) -> Path:
    """Write an event payload to a temporary JSON file.

    Parameters
    ----------
    tmp_path : Path
        pytest temporary directory fixture.
    payload : dict
        The event payload to serialise.

    Returns
    -------
    Path
        Path to the created event.json file.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    return event_path


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestNormalizeMergeMethod:
    """Tests for _normalize_merge_method."""

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("squash", "SQUASH"),
            ("SQUASH", "SQUASH"),
            ("  squash  ", "SQUASH"),
            ("merge", "MERGE"),
            ("rebase", "REBASE"),
        ],
    )
    def test_valid_values(self, input_value: str, expected: str) -> None:
        """Valid merge methods are normalised correctly."""
        result = dependabot_automerge._normalize_merge_method(input_value)
        assert result == expected, f"Expected {expected} for input '{input_value}'"

    def test_invalid_value_raises(self) -> None:
        """Invalid merge methods cause a failure."""
        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._normalize_merge_method("invalid")


class TestNormalizeLabel:
    """Tests for _normalize_label."""

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("dependencies", "dependencies"),
            ("  dependencies  ", "dependencies"),
            ("", None),
            ("   ", None),
            (None, None),
        ],
    )
    def test_whitespace_handling(
        self, input_value: str | None, expected: str | None
    ) -> None:
        """Labels are trimmed and empty strings become None."""
        result = dependabot_automerge._normalize_label(input_value)
        assert result == expected, f"Expected {expected!r} for input {input_value!r}"


class TestResolveRepository:
    """Tests for _resolve_repository."""

    def test_explicit_repository_used(self) -> None:
        """Explicit repository input takes precedence."""
        result = dependabot_automerge._resolve_repository(
            "explicit/repo", {"repository": {"full_name": "event/repo"}}
        )
        assert result == "explicit/repo", "Explicit repo should take precedence"

    def test_event_fallback(self) -> None:
        """Falls back to event payload when no explicit repo."""
        result = dependabot_automerge._resolve_repository(
            None, {"repository": {"full_name": "event/repo"}}
        )
        assert result == "event/repo", "Should fall back to event repo"

    def test_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to GITHUB_REPOSITORY env var."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "env/repo")
        result = dependabot_automerge._resolve_repository(None, None)
        assert result == "env/repo", "Should fall back to env var"

    def test_missing_repository_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises when no repository can be determined."""
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._resolve_repository(None, None)


class TestResolvePullRequestNumber:
    """Tests for _resolve_pull_request_number."""

    def test_explicit_number_used(self) -> None:
        """Explicit PR number takes precedence."""
        result = dependabot_automerge._resolve_pull_request_number(
            42, {"pull_request": {"number": 99}}
        )
        assert result == 42, "Explicit number should take precedence"

    def test_event_fallback(self) -> None:
        """Falls back to event payload when no explicit number."""
        result = dependabot_automerge._resolve_pull_request_number(
            None, {"pull_request": {"number": 99}}
        )
        assert result == 99, "Should fall back to event number"

    def test_string_number_parsed(self) -> None:
        """String numbers in event are converted to int."""
        result = dependabot_automerge._resolve_pull_request_number(
            None, {"pull_request": {"number": "123"}}
        )
        assert result == 123, "String number should be parsed"

    def test_missing_number_raises(self) -> None:
        """Raises when no PR number can be determined."""
        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._resolve_pull_request_number(None, {})


# ---------------------------------------------------------------------------
# GraphQL request tests
# ---------------------------------------------------------------------------


class TestRequestGraphql:
    """Tests for _request_graphql error handling."""

    def test_http_error_after_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP errors are retried and then fail."""
        connect_error = httpx.ConnectError("Connection failed")

        def raise_error(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise connect_error

        monkeypatch.setattr(httpx.Client, "post", raise_error)
        monkeypatch.setattr(dependabot_automerge, "_backoff_sleep", lambda *_: None)

        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._request_graphql(TEST_TOKEN, "query {}", {})

    def test_client_error_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """4xx errors fail immediately without retry."""
        call_count = 0

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, text="Bad Request")

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._request_graphql(TEST_TOKEN, "query {}", {})

        assert call_count == 1, "4xx errors should not be retried"

    def test_non_json_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-JSON responses cause a failure."""

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._request_graphql(TEST_TOKEN, "query {}", {})

    def test_graphql_errors_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GraphQL errors in response cause a failure."""

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200, json={"errors": [{"message": "Something went wrong"}]}
            )

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            dependabot_automerge._request_graphql(TEST_TOKEN, "query {}", {})


# ---------------------------------------------------------------------------
# Dry-run skip tests (parametrised)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "test_case",
    [
        DryRunTestCase(
            event_data={
                "pull_request": {
                    "number": 7,
                    "draft": False,
                    "user": {"login": "someone"},
                    "labels": [{"name": "dependencies"}],
                },
                "repository": {"full_name": "acme/example"},
            },
            expected_reason="author-not-dependabot",
            test_id="non_dependabot_author",
        ),
        DryRunTestCase(
            event_data={
                "pull_request": {
                    "number": 8,
                    "draft": True,
                    "user": {"login": "dependabot[bot]"},
                    "labels": [{"name": "dependencies"}],
                },
                "repository": {"full_name": "acme/example"},
            },
            expected_reason="draft-pr",
            test_id="draft_pr",
        ),
        DryRunTestCase(
            event_data={
                "pull_request": {
                    "number": 10,
                    "draft": False,
                    "user": {"login": "dependabot[bot]"},
                    "labels": [{"name": "other-label"}],
                },
                "repository": {"full_name": "acme/example"},
            },
            expected_reason="missing-label:dependencies",
            test_id="missing_label",
        ),
    ],
    ids=lambda tc: tc.test_id,
)
def test_dry_run_skips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    test_case: DryRunTestCase,
) -> None:
    """Dry-run mode skips PRs that don't meet eligibility criteria."""
    event_path = _write_event(tmp_path, test_case.event_data)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            dry_run=True,
            required_label="dependencies",
        ),
    )

    captured = capsys.readouterr()
    assert "automerge_status=skipped" in captured.out, (
        f"[{test_case.test_id}] Expected skipped status"
    )
    assert f"automerge_reason={test_case.expected_reason}" in captured.out, (
        f"[{test_case.test_id}] Expected reason {test_case.expected_reason}"
    )


# ---------------------------------------------------------------------------
# Other integration tests
# ---------------------------------------------------------------------------


def test_dry_run_requires_event_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run mode errors without a pull_request event payload."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/example")

    with pytest.raises(SystemExit, match="1"):
        dependabot_automerge.main(
            github_token=TEST_TOKEN,
            options=dependabot_automerge.AutomergeOptions(
                dry_run=True,
                required_label="dependencies",
            ),
        )

    captured = capsys.readouterr()
    assert "automerge_status=error" in captured.err, "Expected error status"
    assert "Dry-run mode requires GITHUB_EVENT_PATH" in captured.err, (
        "Expected specific error message"
    )


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
        options=dependabot_automerge.AutomergeOptions(
            dry_run=True,
            required_label="dependencies",
        ),
    )

    captured = capsys.readouterr()
    assert "automerge_status=dry-run" in captured.out, "Expected dry-run status"
    assert "automerge_reason=eligible" in captured.out, "Expected eligible reason"


def test_enables_automerge_when_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Eligible PRs enable auto-merge via the GitHub API."""

    def fake_request(
        _token: str, query: str, _variables: dict[str, object]
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
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=12,
        ),
    )

    captured = capsys.readouterr()
    assert "automerge_status=enabled" in captured.out, "Expected enabled status"
    assert "automerge_reason=enabled" in captured.out, "Expected enabled reason"


def test_already_enabled_automerge_detected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PRs with auto-merge already enabled are detected."""

    def fake_request(
        _token: str, _query: str, _variables: dict[str, object]
    ) -> dict[str, object]:
        return {
            "repository": {
                "pullRequest": {
                    "id": "PR_15",
                    "number": 15,
                    "isDraft": False,
                    "author": {"login": "dependabot[bot]"},
                    "labels": {"nodes": [{"name": "dependencies"}]},
                    "autoMergeRequest": {
                        "enabledAt": "2024-01-01T00:00:00Z",
                        "mergeMethod": "SQUASH",
                    },
                }
            }
        }

    monkeypatch.setattr(dependabot_automerge, "_request_graphql", fake_request)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=15,
        ),
    )

    captured = capsys.readouterr()
    assert "automerge_status=enabled" in captured.out, "Expected enabled status"
    assert "automerge_reason=already-enabled" in captured.out, (
        "Expected already-enabled reason"
    )
