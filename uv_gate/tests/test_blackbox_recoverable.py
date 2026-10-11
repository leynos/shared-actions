"""Black-box tests for the recoverable misses that are not a plain cache miss.

``missing-python`` (a managed interpreter that must be downloaded) and
``missing-git-source`` (a Git source that is not cached) each earn the one online
attempt in ``prepare`` and ``tool``, never in ``run``. The same module checks the
direct-reference pin form through the ``tool`` command line.
"""

from __future__ import annotations

import pytest
from _harness import (
    OFFLINE_SYNC,
    ONLINE_SYNC,
    Harness,
    fixture_text,
    offline_then_online,
    response,
    rule,
)

RECOVERABLE = [
    ("missing_python", "missing-python"),
    ("missing_git_source", "missing-git-source"),
]


@pytest.mark.parametrize(("fixture", "label"), RECOVERABLE)
def test_prepare_goes_online_exactly_once(
    harness: Harness, fixture: str, label: str
) -> None:
    """A miss that going online can fix earns one online sync, and it is named."""
    result = harness.run(["prepare"], offline_then_online(fixture))
    assert result.status == 0
    assert result.argvs() == [OFFLINE_SYNC, ONLINE_SYNC]
    assert label in result.stderr


@pytest.mark.parametrize(("fixture", "label"), RECOVERABLE)
def test_prepare_retry_is_bounded(harness: Harness, fixture: str, label: str) -> None:
    """If the online sync fails the same way, it stops after two calls."""
    result = harness.run(["prepare"], offline_then_online(fixture, fixture))
    assert result.status == 1
    assert len(result.argvs()) == 2
    assert label in result.stderr


@pytest.mark.parametrize(("fixture", "_label"), RECOVERABLE)
def test_prepare_respects_the_online_switch(
    harness: Harness, fixture: str, _label: str
) -> None:
    """UV_GATE_ALLOW_ONLINE=0 forbids the download or fetch."""
    result = harness.run(
        ["prepare"], offline_then_online(fixture), env={"UV_GATE_ALLOW_ONLINE": "0"}
    )
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC]


@pytest.mark.parametrize(("fixture", "_label"), RECOVERABLE)
def test_tool_respects_the_online_switch(
    harness: Harness, fixture: str, _label: str
) -> None:
    """UV_GATE_ALLOW_ONLINE=0 forbids the download or fetch for a tool too."""
    rules = [
        rule(["tool", "run", "--offline"], response(1, fixture_text(fixture))),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    result = harness.run(
        ["tool", "--from", "ruff==1", "--", "ruff"],
        rules,
        env={"UV_GATE_ALLOW_ONLINE": "0"},
    )
    assert result.status == 1
    assert len(result.argvs()) == 1


def test_prepare_near_miss_for_a_missing_python_is_not_retried(
    harness: Harness,
) -> None:
    """The header without the download hint is `unknown`, so one call only."""
    rules = [rule(OFFLINE_SYNC, response(1, fixture_text("missing_python_near_miss")))]
    result = harness.run(["prepare"], rules)
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC]


@pytest.mark.parametrize(
    "fixture", ["git_operation_failed_no_reason", "git_fetches_forbidden_elsewhere"]
)
def test_prepare_git_near_misses_are_not_retried(
    harness: Harness, fixture: str
) -> None:
    """Without uv's reason, a Git failure is `unknown`: one call, no retry."""
    rules = [rule(OFFLINE_SYNC, response(1, fixture_text(fixture)))]
    result = harness.run(["prepare"], rules)
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC]


def test_tool_warms_online_once_on_a_missing_python(harness: Harness) -> None:
    """A tool whose managed interpreter must be downloaded is warmed once."""
    rules = [
        rule(["tool", "run", "--offline"], response(1, fixture_text("missing_python"))),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    result = harness.run(
        [
            "tool",
            "--managed-python",
            "--python",
            "pypy@3.12",
            "--from",
            "pylint==4",
            "--",
            "pylint",
        ],
        rules,
    )
    assert result.status == 0
    offline, online = result.argvs()
    assert "--offline" in offline
    assert "--offline" not in online
    managed = ["--managed-python", "--python", "pypy@3.12", "--from", "pylint==4"]
    assert online[2:] == [*managed, "pylint"]
    assert "missing-python" in result.stderr


def test_tool_near_miss_for_a_missing_python_is_not_retried(harness: Harness) -> None:
    """The header without the download hint stays `unknown` for a tool too."""
    rules = [
        rule(
            ["tool", "run"],
            response(1, fixture_text("missing_python_near_miss")),
        )
    ]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 1
    assert len(result.argvs()) == 1


def test_tool_warms_online_once_on_a_missing_git_source(harness: Harness) -> None:
    """A tool whose Git source is not cached is warmed once, with its own options."""
    rules = [
        rule(
            ["tool", "run", "--offline"],
            response(1, fixture_text("missing_git_source")),
        ),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    spec = f"git+https://example.invalid/x.git@{'0123456789abcdef' * 2}01234567"
    result = harness.run(["tool", "--python", "3.13", "--from", spec, "--", "x"], rules)
    assert result.status == 0
    offline, online = result.argvs()
    assert "--offline" in offline
    assert "--offline" not in online
    assert online[2:] == ["--python", "3.13", "--from", spec, "x"]
    assert "missing-git-source" in result.stderr


DIRECT_SPEC = (
    f"tool[extra] @ git+https://example.invalid/t.git@{'0123456789abcdef' * 2}01234567"
)


def test_tool_accepts_a_direct_reference_at_a_full_commit(harness: Harness) -> None:
    """The extras form is a pin: uv runs once, offline, with the spec unchanged."""
    result = harness.run(
        ["tool", "--from", DIRECT_SPEC, "--", "tool", "--version"],
        [rule(["tool", "run"], response(0))],
    )
    assert result.status == 0
    assert result.argvs() == [
        ["tool", "run", "--offline", "--from", DIRECT_SPEC, "tool", "--version"]
    ]


@pytest.mark.parametrize(
    "spec",
    [
        "tool[extra] @ git+https://example.invalid/t.git@main",
        "tool[extra] @ git+https://example.invalid/t.git@v1.2.3",
        "tool[extra] @ git+https://example.invalid/t.git@0123456",
        "tool[extra] @ https://example.invalid/tool-1.0.tar.gz",
    ],
    ids=["branch", "tag", "short-sha", "not-git"],
)
def test_tool_refuses_a_direct_reference_that_is_not_a_full_commit(
    harness: Harness, spec: str
) -> None:
    """A branch, tag, short SHA or non-Git URL is refused before uv is called."""
    result = harness.run(
        ["tool", "--from", spec, "--", "tool"], [rule(["tool", "run"], response(0))]
    )
    assert result.status == 2
    assert "not pinned" in result.stderr
    assert result.argvs() == []
