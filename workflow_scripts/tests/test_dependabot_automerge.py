"""Unit tests for the dependabot auto-merge helper script."""

from __future__ import annotations

import dataclasses
import json
import typing as typ

import httpx
import pytest

from workflow_scripts import (
    dependabot_automerge,
    dependabot_commit_audit,
    dependabot_decision,
    graphql_client,
)

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


@dataclasses.dataclass(frozen=True, slots=True)
class LiveExecutionTestCase:
    """Test case parameters for live execution scenarios."""

    pr_number: int
    merge_state_status: str | None
    mergeable: str | None
    auto_merge_request: dict[str, object] | None
    should_enable: bool
    expected_status: str
    expected_reason: str
    test_id: str
    should_merge: bool = False


def _write_event(tmp_path: Path, payload: dict[str, object]) -> Path:
    """Write an event payload to a temporary JSON file.

    Parameters
    ----------
    tmp_path : Path
        pytest temporary directory fixture.
    payload : dict
        The event payload to serialize.

    Returns
    -------
    Path
        Path to the created event.json file.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    return event_path


def _build_live_execution_mock(
    test_case: LiveExecutionTestCase,
) -> typ.Callable[[str, str, dict[str, object]], dict[str, object]]:
    """Build a mock GraphQL handler for live execution tests."""
    calls = {"enable": 0, "merge": 0}

    def handler(
        _token: str, query: str, _variables: dict[str, object]
    ) -> dict[str, object]:
        if "enablePullRequestAutoMerge" in query:
            calls["enable"] += 1
            return {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"number": test_case.pr_number}
                }
            }
        if "mergePullRequest" in query:
            calls["merge"] += 1
            return {
                "mergePullRequest": {
                    "pullRequest": {"number": test_case.pr_number, "merged": True}
                }
            }
        return {
            "repository": {
                "pullRequest": {
                    "id": f"PR_{test_case.pr_number}",
                    "number": test_case.pr_number,
                    "isDraft": False,
                    "mergeStateStatus": test_case.merge_state_status,
                    "mergeable": test_case.mergeable,
                    "author": {"login": "dependabot[bot]"},
                    "labels": {"nodes": [{"name": "dependencies"}]},
                    "autoMergeRequest": test_case.auto_merge_request,
                }
            }
        }

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


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
        """Valid merge methods are normalized correctly."""
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
    """Tests for request_graphql error handling."""

    def test_http_error_after_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP errors are retried and then fail."""
        connect_error = httpx.ConnectError("Connection failed")

        def raise_error(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise connect_error

        monkeypatch.setattr(httpx.Client, "post", raise_error)
        monkeypatch.setattr(graphql_client, "_backoff_sleep", lambda *_: None)

        with pytest.raises(SystemExit, match="1"):
            graphql_client.request_graphql(TEST_TOKEN, "query {}", {})

    def test_client_error_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """4xx errors fail immediately without retry."""
        call_count = 0

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, text="Bad Request")

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            graphql_client.request_graphql(TEST_TOKEN, "query {}", {})

        assert call_count == 1, "4xx errors should not be retried"

    def test_non_json_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-JSON responses cause a failure."""

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            graphql_client.request_graphql(TEST_TOKEN, "query {}", {})

    def test_graphql_errors_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GraphQL errors in response cause a failure."""

        def mock_post(*_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200, json={"errors": [{"message": "Something went wrong"}]}
            )

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        with pytest.raises(SystemExit, match="1"):
            graphql_client.request_graphql(TEST_TOKEN, "query {}", {})


# ---------------------------------------------------------------------------
# Dry-run skip tests (parametrized)
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


@pytest.mark.parametrize("login", ["dependabot[bot]", "dependabot"])
def test_dry_run_eligible_dependabot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    login: str,
) -> None:
    """Eligible Dependabot PRs log dry-run readiness."""
    event = {
        "pull_request": {
            "number": 9,
            "draft": False,
            "user": {"login": login},
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


@pytest.mark.parametrize(
    "test_case",
    [
        LiveExecutionTestCase(
            pr_number=12,
            merge_state_status="HAS_HOOKS",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=False,
            expected_status="merged",
            expected_reason="merged-directly",
            test_id="merge_state_has_hooks_merges_directly",
            should_merge=True,
        ),
        LiveExecutionTestCase(
            pr_number=13,
            merge_state_status="DIRTY",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=False,
            expected_status="skipped",
            expected_reason="merge-state-dirty",
            test_id="merge_state_dirty_skips",
        ),
        LiveExecutionTestCase(
            pr_number=14,
            merge_state_status="BLOCKED",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=True,
            expected_status="enabled",
            expected_reason="enabled",
            test_id="merge_state_blocked_enables",
        ),
        LiveExecutionTestCase(
            pr_number=15,
            merge_state_status="BEHIND",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=False,
            expected_status="skipped",
            expected_reason="merge-state-behind",
            test_id="merge_state_behind_skips",
        ),
        LiveExecutionTestCase(
            pr_number=22,
            merge_state_status="UNSTABLE",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=False,
            expected_status="merged",
            expected_reason="merged-directly",
            test_id="merge_state_unstable_merges_directly",
            should_merge=True,
        ),
        LiveExecutionTestCase(
            pr_number=24,
            merge_state_status="CLEAN",
            mergeable="MERGEABLE",
            auto_merge_request=None,
            should_enable=False,
            expected_status="merged",
            expected_reason="merged-directly",
            test_id="merge_state_clean_merges_directly",
            should_merge=True,
        ),
        LiveExecutionTestCase(
            pr_number=25,
            merge_state_status="MERGED",
            mergeable="UNKNOWN",
            auto_merge_request=None,
            should_enable=False,
            expected_status="skipped",
            expected_reason="already-merged",
            test_id="merge_state_merged_skips",
        ),
        LiveExecutionTestCase(
            pr_number=23,
            merge_state_status="DIRTY",
            mergeable="CONFLICTING",
            auto_merge_request=None,
            should_enable=False,
            expected_status="skipped",
            expected_reason="mergeable-conflicting",
            test_id="mergeable_conflicting_skips",
        ),
        LiveExecutionTestCase(
            pr_number=16,
            merge_state_status="CLEAN",
            mergeable="MERGEABLE",
            auto_merge_request={
                "enabledAt": "2024-01-01T00:00:00Z",
                "mergeMethod": "SQUASH",
            },
            should_enable=False,
            expected_status="enabled",
            expected_reason="already-enabled",
            test_id="already_enabled_detected",
        ),
    ],
    ids=lambda tc: tc.test_id,
)
def test_live_execution_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    test_case: LiveExecutionTestCase,
) -> None:
    """Live execution scenarios share common GraphQL mock wiring."""
    handler = _build_live_execution_mock(test_case)
    monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=test_case.pr_number,
        ),
    )

    captured = capsys.readouterr()
    assert f"automerge_status={test_case.expected_status}" in captured.out, (
        f"[{test_case.test_id}] Expected {test_case.expected_status} status"
    )
    assert f"automerge_reason={test_case.expected_reason}" in captured.out, (
        f"[{test_case.test_id}] Expected reason {test_case.expected_reason}"
    )
    enable_calls = handler.calls["enable"]  # type: ignore[attr-defined]
    expected_calls = 1 if test_case.should_enable else 0
    assert enable_calls == expected_calls, (
        f"[{test_case.test_id}] Expected enable calls to be {expected_calls}"
    )
    merge_calls = handler.calls["merge"]  # type: ignore[attr-defined]
    expected_merge_calls = 1 if test_case.should_merge else 0
    assert merge_calls == expected_merge_calls, (
        f"[{test_case.test_id}] Expected merge calls to be {expected_merge_calls}"
    )


def test_retries_until_merge_state_known(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mergeability UNKNOWN is retried before enabling auto-merge."""
    calls: dict[str, int] = {"fetch": 0}
    monkeypatch.setattr(dependabot_automerge.time, "sleep", lambda _seconds: None)

    def fake_request(
        _token: str, query: str, _variables: dict[str, object]
    ) -> dict[str, object]:
        if "enablePullRequestAutoMerge" in query:
            return {"enablePullRequestAutoMerge": {"pullRequest": {"number": 21}}}
        calls["fetch"] += 1
        merge_state = "UNKNOWN" if calls["fetch"] == 1 else "BLOCKED"
        mergeable_state = "UNKNOWN" if calls["fetch"] == 1 else "MERGEABLE"
        return {
            "repository": {
                "pullRequest": {
                    "id": "PR_21",
                    "number": 21,
                    "isDraft": False,
                    "mergeStateStatus": merge_state,
                    "mergeable": mergeable_state,
                    "author": {"login": "dependabot[bot]"},
                    "labels": {"nodes": [{"name": "dependencies"}]},
                    "autoMergeRequest": None,
                }
            }
        }

    monkeypatch.setattr(dependabot_automerge, "request_graphql", fake_request)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=21,
        ),
    )

    captured = capsys.readouterr()
    assert calls["fetch"] >= 2, "Expected merge state to be refreshed"
    assert "automerge_status=enabled" in captured.out, "Expected enabled status"


class PullRequestOverrides(typ.TypedDict, total=False):
    """The fields :func:`_pr` accepts, with their real types.

    A ``dict[str, typ.Any]`` here would take a misspelled field name or a
    wrongly typed value from every caller without Pyright noticing, which
    is the opposite of what a fixture builder is for.
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None
    auto_merge_enabled: bool
    merge_state_status: dependabot_automerge.MergeStateStatus
    mergeable_state: dependabot_automerge.MergeableState
    foreign_commits: tuple[dependabot_commit_audit.ForeignCommit, ...]
    commits_readable: bool


def _pr(
    **overrides: typ.Unpack[PullRequestOverrides],
) -> dependabot_decision.PullRequestContext:
    """Build a PullRequestContext with eligible defaults."""
    values: PullRequestOverrides = {
        "number": 1,
        "owner": "leynos",
        "repo": "rstest-bdd",
        "author": "dependabot[bot]",
        "is_draft": False,
        "labels": (),
    }
    values.update(overrides)
    return dependabot_decision.PullRequestContext(**values)


def _commit_node(oid: str, *logins: str) -> dict[str, object]:
    """Build one commit node as the GraphQL query returns it."""
    return {
        "commit": {
            "oid": oid,
            "authors": {"nodes": [{"user": {"login": login}} for login in logins]},
        }
    }


class TestForeignCommitExtraction:
    """Reading who wrote each commit on the branch."""

    def test_an_all_dependabot_branch_has_none(self) -> None:
        """The ordinary bump, which must keep merging unattended."""
        payload = {
            "commits": {
                "nodes": [
                    _commit_node("aaaaaaaa1111", "dependabot[bot]"),
                    _commit_node("bbbbbbbb2222", "dependabot"),
                ]
            }
        }

        assert dependabot_commit_audit.audit_commits(payload).foreign == (), (
            "both logins are Dependabot's, so nothing is foreign"
        )

    def test_a_maintainer_commit_is_foreign(self) -> None:
        """The case this exists for: a human fix pushed onto the branch."""
        payload = {
            "commits": {
                "nodes": [
                    _commit_node("aaaaaaaa1111", "dependabot[bot]"),
                    _commit_node("cccccccc3333", "leynos"),
                ]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.oid for commit in found] == ["cccccccc3333"], found
        assert found[0].author == "leynos", found

    def test_a_co_authored_commit_is_foreign(self) -> None:
        """Dependabot plus a human is still a human's change.

        A commit crediting both would pass a check that asked only
        whether Dependabot appears among the authors.
        """
        payload = {
            "commits": {
                "nodes": [_commit_node("dddd4444", "dependabot[bot]", "leynos")]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.author for commit in found] == ["leynos"], found

    def test_an_unnamed_author_is_foreign(self) -> None:
        """A commit GitHub cannot attribute is not evidence of a bot."""
        payload = {
            "commits": {
                "nodes": [{"commit": {"oid": "eeee5555", "authors": {"nodes": [{}]}}}]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.author for commit in found] == [
            dependabot_commit_audit.UNKNOWN_AUTHOR
        ], found

    @pytest.mark.parametrize(
        "payload",
        [{}, {"commits": None}, {"commits": {"nodes": None}}],
        ids=["absent", "null-commits", "null-nodes"],
    )
    def test_an_unreadable_commit_list_blocks_nothing(
        self, payload: dict[str, object]
    ) -> None:
        """Unknown is not the same as foreign.

        A query change that stopped returning commits would otherwise
        halt every consumer's automerge at once, which is a worse
        failure than the one this check prevents.
        """
        audit = dependabot_commit_audit.audit_commits(payload)

        assert audit.foreign == (), payload
        assert not audit.readable, (
            "an unreadable list must be distinguishable from a clean branch, "
            "or the loss of the check is silent"
        )

    def test_a_readable_list_says_so(self) -> None:
        """The ordinary case must not look like a failure to read."""
        payload = {"commits": {"nodes": [_commit_node("aaaa1111", "dependabot[bot]")]}}

        assert dependabot_commit_audit.audit_commits(payload).readable, payload


class TestForeignCommitsBlockAutomerge:
    """The verdict, and what it tells the maintainer."""

    def test_a_clean_branch_stays_eligible(self) -> None:
        """The ordinary bump is unaffected."""
        decision = dependabot_decision.evaluate(_pr(), None)

        assert decision.status == "ready", decision
        assert decision.reason == "eligible", decision

    def test_a_foreign_commit_skips_and_names_it(self) -> None:
        """Opening a pull request is not the same as writing what is in it."""
        pr = _pr(
            foreign_commits=(
                dependabot_commit_audit.ForeignCommit("cccccccc3333", "leynos"),
            )
        )

        decision = dependabot_decision.evaluate(pr, None)

        assert decision.status == "skipped", decision
        assert decision.reason == "foreign-commit:cccccccc", decision

    def test_the_annotation_names_the_commit_and_the_remedy(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A maintainer must learn why, not merely that it stopped."""
        pr = _pr(
            foreign_commits=(
                dependabot_commit_audit.ForeignCommit("cccccccc3333", "leynos"),
            )
        )

        dependabot_decision.emit_decision(
            pr,
            dependabot_decision.Decision(
                status="skipped", reason="foreign-commit:cccccccc"
            ),
            config=dependabot_decision.AutomergeConfig(
                merge_method="squash", required_label=None, dry_run=False
            ),
        )

        out = capsys.readouterr().out
        assert "::notice title=dependabot-automerge::" in out, out
        assert "cccccccc by leynos" in out, out
        assert "its own pull request" in out, out


def test_an_unreadable_commit_list_is_announced(
    capsys: pytest.CaptureFixture,
) -> None:
    """Failing open must be loud.

    The check is allowed to pass a branch it cannot inspect, because
    refusing on unknown would stop every consumer's automerge at once.
    What it may not do is lose the protection quietly.
    """
    dependabot_decision.emit_decision(
        _pr(commits_readable=False),
        dependabot_decision.Decision(status="ready", reason="eligible"),
        config=dependabot_decision.AutomergeConfig(
            merge_method="squash", required_label=None, dry_run=False
        ),
    )

    out = capsys.readouterr().out
    assert "::warning title=dependabot-automerge::" in out, out
    assert "did not run" in out, out
    assert "author alone" in out, out
