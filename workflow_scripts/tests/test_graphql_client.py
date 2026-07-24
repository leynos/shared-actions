"""Unit tests for the GitHub GraphQL client retry and backoff logic.

These tests pin the retry and backoff boundaries harvested in #336; the
response-parsing, rate-limit, and status-code survivors remain that
issue's remit.
"""

from __future__ import annotations

import pytest

from workflow_scripts import graphql_client

# Test-only constant (not a real credential)
TEST_TOKEN = "test-token"  # noqa: S105


class TestShouldRetry:
    """Boundary of the retry-budget predicate."""

    def test_retries_remain_below_the_limit(self) -> None:
        """An attempt below the limit still has retries left."""
        assert graphql_client._should_retry(2, 3) is True, (
            "an attempt below max_retries should still retry"
        )

    def test_final_attempt_does_not_retry(self) -> None:
        """The attempt equal to the limit is the last (strict boundary)."""
        assert graphql_client._should_retry(3, 3) is False, (
            "the attempt equal to max_retries must not retry"
        )


class TestBackoffSleep:
    """Exponential backoff schedule."""

    @pytest.mark.parametrize(("attempt", "expected"), [(0, 1.0), (3, 8.0)])
    def test_exponential_schedule(
        self, monkeypatch: pytest.MonkeyPatch, attempt: int, expected: float
    ) -> None:
        """Backoff sleeps ``base * 2**attempt`` seconds for each attempt."""
        recorded: list[float] = []
        monkeypatch.setattr(graphql_client.time, "sleep", recorded.append)
        graphql_client._backoff_sleep(attempt)
        assert recorded == [expected], (
            f"attempt {attempt} should back off {expected} seconds"
        )


class TestAttemptRetryOrFail:
    """The retry-or-fail decision that guards estate-wide automerge."""

    def test_backs_off_on_the_current_attempt_when_retries_remain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A remaining retry backs off on the current attempt without failing."""
        recorded: list[int] = []
        monkeypatch.setattr(graphql_client, "_backoff_sleep", recorded.append)
        graphql_client._attempt_retry_or_fail(0, 3, "boom")
        assert recorded == [0], (
            "a remaining retry should back off on the current attempt, not fail"
        )

    def test_fails_with_message_when_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An exhausted budget fails with the supplied error message."""
        monkeypatch.setattr(graphql_client, "_backoff_sleep", lambda *_: None)
        with pytest.raises(SystemExit) as excinfo:
            graphql_client._attempt_retry_or_fail(3, 3, "budget-exhausted-message")
        assert excinfo.value.code == 1, "an exhausted budget should exit with code 1"
        assert "budget-exhausted-message" in capsys.readouterr().err, (
            "the failure should carry the supplied error message"
        )


class TestRequestGraphqlRetries:
    """The retry loop's attempt budget."""

    def test_connection_errors_retry_then_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A persistent connection error retries three times, then fails."""
        seen: list[tuple[object, object, object]] = []

        def fake_execute(token: str, query: str, variables: dict[str, object]) -> None:
            seen.append((token, query, variables))

        monkeypatch.setattr(graphql_client, "_execute_graphql_attempt", fake_execute)
        monkeypatch.setattr(graphql_client, "_backoff_sleep", lambda *_: None)
        variables: dict[str, object] = {"n": 1}
        with pytest.raises(SystemExit):
            graphql_client.request_graphql(TEST_TOKEN, "query {}", variables)
        assert len(seen) == 4, (
            "one initial attempt plus three retries should run before failing"
        )
        assert seen[0] == (TEST_TOKEN, "query {}", variables), (
            "the token, query, and variables should reach each attempt unchanged"
        )
