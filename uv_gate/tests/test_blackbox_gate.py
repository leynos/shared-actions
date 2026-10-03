"""Black-box tests: run ``uv_gate.py`` against a scripted fake ``uv``.

Each failure class is exercised end to end. The recorded call list is the
evidence for the central rule: the helper makes one offline attempt, goes
online at most once, and only for a proven cache miss.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _harness import Harness, fixture_text, other_device_dir, response, rule

OFFLINE_SYNC = ["sync", "--locked", "--offline"]
ONLINE_SYNC = ["sync", "--locked"]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """Return a fresh harness rooted in the test's temporary directory."""
    return Harness(tmp_path)


def offline_then_online(offline: str, online: str | None = None) -> list[dict]:
    """Script an offline failure followed by an online result."""
    rules = [rule(OFFLINE_SYNC, response(1, fixture_text(offline)))]
    rules.append(
        rule(
            ONLINE_SYNC,
            response(0) if online is None else response(1, fixture_text(online)),
            without=("--offline",),
        )
    )
    return rules


# prepare ---------------------------------------------------------------------


def test_prepare_success_makes_one_offline_call(harness: Harness) -> None:
    """A prepared cache needs no network and exactly one uv call."""
    result = harness.run(["prepare"], [rule(OFFLINE_SYNC, response(0))])
    assert result.status == 0
    assert result.argvs() == [OFFLINE_SYNC]


def test_prepare_passes_sync_arguments_through(harness: Harness) -> None:
    """Groups and the Python version reach uv unchanged, offline and online."""
    result = harness.run(
        ["prepare", "--group", "dev", "--python", "3.13"],
        offline_then_online("cache_miss"),
    )
    assert result.argvs() == [
        [*OFFLINE_SYNC, "--group", "dev", "--python", "3.13"],
        [*ONLINE_SYNC, "--group", "dev", "--python", "3.13"],
    ]


def test_prepare_cache_miss_goes_online_exactly_once(harness: Harness) -> None:
    """An absent cache entry triggers one online `uv sync --locked`."""
    result = harness.run(["prepare"], offline_then_online("cache_miss"))
    assert result.status == 0
    assert result.argvs() == [OFFLINE_SYNC, ONLINE_SYNC]
    assert "cache-miss" in result.stderr
    assert "one online" in result.stderr


def test_prepare_online_retry_is_bounded(harness: Harness) -> None:
    """If the online sync also reports a miss, there is no third attempt."""
    rules = [
        rule(OFFLINE_SYNC, response(1, fixture_text("cache_miss"))),
        rule(
            ONLINE_SYNC,
            response(1, fixture_text("cache_miss")),
            without=("--offline",),
        ),
    ]
    result = harness.run(["prepare"], rules)
    assert result.status == 1
    assert len(result.argvs()) == 2


def test_prepare_ambiguous_offline_resolution_ends_in_stale_lock(
    harness: Harness,
) -> None:
    """Offline, a stale lock looks like a miss; the online attempt names it."""
    result = harness.run(
        ["prepare"], offline_then_online("stale_offline_ambiguous", "stale_lock_online")
    )
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC, ONLINE_SYNC]
    assert "stale-lock" in result.stderr
    assert "uv lock" in result.stderr
    assert all(call[:1] != ["lock"] for call in result.argvs()), "never refreshes"


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("auth_missing_repo", "auth"),
        ("missing_revision", "missing-revision"),
        ("missing_package", "missing-package"),
        ("stale_lock_online", "stale-lock"),
        ("missing_lock", "missing-lock"),
    ],
)
def test_prepare_never_retries_other_failures(
    harness: Harness, fixture: str, kind: str
) -> None:
    """Auth, missing package/revision, stale and missing lock stop at once."""
    result = harness.run(
        ["prepare"], [rule(OFFLINE_SYNC, response(1, fixture_text(fixture)))]
    )
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC]
    assert f"uv-gate: {kind}:" in result.stderr


def test_prepare_unrecognized_failure_is_not_retried(harness: Harness) -> None:
    """A failure the helper cannot classify is passed through, once."""
    result = harness.run(
        ["prepare"], [rule(OFFLINE_SYNC, response(7, "something odd\n"))]
    )
    assert result.status == 7
    assert result.argvs() == [OFFLINE_SYNC]


def test_prepare_online_step_can_be_forbidden(harness: Harness) -> None:
    """UV_GATE_ALLOW_ONLINE=0 turns the one online step into a failure."""
    result = harness.run(
        ["prepare"],
        offline_then_online("cache_miss"),
        env={"UV_GATE_ALLOW_ONLINE": "0"},
    )
    assert result.status == 1
    assert result.argvs() == [OFFLINE_SYNC]
    assert "UV_GATE_ALLOW_ONLINE=0" in result.stderr


def test_prepare_preserves_uv_output_before_the_summary(harness: Harness) -> None:
    """The helper passes uv's own stderr through, and the summary line follows it."""
    result = harness.run(
        ["prepare"], [rule(OFFLINE_SYNC, response(1, fixture_text("missing_lock")))]
    )
    uv_text = fixture_text("missing_lock").strip()
    assert result.stderr.index(uv_text) < result.stderr.index("uv-gate: missing-lock")


# run -------------------------------------------------------------------------


def test_run_is_frozen_offline_and_single(harness: Harness) -> None:
    """The gate runs `uv run --frozen --offline <command>` once."""
    result = harness.run(
        ["run", "--group", "dev", "--", "pytest", "-q"],
        [rule(["run"], response(0))],
    )
    assert result.status == 0
    assert result.argvs() == [
        ["run", "--frozen", "--offline", "--group", "dev", "pytest", "-q"]
    ]


def test_run_preserves_the_exit_status(harness: Harness) -> None:
    """A failing gate's status is the helper's status."""
    result = harness.run(["run", "--", "pytest"], [rule(["run"], response(3))])
    assert result.status == 3


@pytest.mark.parametrize(
    "fixture", ["cache_miss", "auth_missing_repo", "stale_lock_online"]
)
def test_run_never_retries_whatever_the_failure(harness: Harness, fixture: str) -> None:
    """Even a cache-miss message in a gate's output causes no second call."""
    result = harness.run(
        ["run", "--", "pytest"], [rule(["run"], response(1, fixture_text(fixture)))]
    )
    assert result.status == 1
    assert len(result.argvs()) == 1


def test_run_refuses_flags_that_refresh_or_go_online(harness: Harness) -> None:
    """Refresh, upgrade and network flags are refused before uv runs."""
    for flag in ("--upgrade", "--refresh", "--no-offline", "--reinstall", "--locked"):
        result = harness.run(
            ["run", flag, "--", "pytest"], [rule(["run"], response(0))]
        )
        assert result.status == 2, flag
        assert flag in result.stderr
    assert result.argvs() == []


def test_run_needs_a_command(harness: Harness) -> None:
    """`run` with nothing after `--` is a refusal."""
    result = harness.run(["run"], [rule(["run"], response(0))])
    assert result.status == 2
    assert result.argvs() == []


# tool ------------------------------------------------------------------------


def test_tool_runs_a_pinned_tool_offline(harness: Harness) -> None:
    """A pinned tool runs once with `--offline`."""
    result = harness.run(
        ["tool", "--from", "ruff==0.16.4", "--", "ruff", "--version"],
        [rule(["tool", "run"], response(0))],
    )
    assert result.argvs() == [
        ["tool", "run", "--offline", "--from", "ruff==0.16.4", "ruff", "--version"]
    ]


def test_tool_accepts_the_at_version_form(harness: Harness) -> None:
    """`typos@1.2.3` is a pin without `--from`."""
    result = harness.run(
        ["tool", "--", "typos@1.2.3", "--version"], [rule(["tool", "run"], response(0))]
    )
    assert result.status == 0


@pytest.mark.parametrize(
    "args",
    [
        ["tool", "--from", "ruff", "--", "ruff"],
        ["tool", "--from", "cibuildwheel>=2.16", "--", "cibuildwheel"],
        ["tool", "--", "typos@latest"],
        ["tool", "--", "ruff"],
    ],
)
def test_tool_refuses_unpinned_specs_without_calling_uv(
    harness: Harness, args: list[str]
) -> None:
    """Unpinned tools are refused before uv is run."""
    result = harness.run(args, [rule(["tool", "run"], response(0))])
    assert result.status == 2
    assert "not pinned" in result.stderr
    assert result.argvs() == []


def test_tool_warms_online_once_on_a_cache_miss(harness: Harness) -> None:
    """A proven miss warms the tool online, once, then it runs."""
    rules = [
        rule(
            ["tool", "run", "--offline"],
            response(1, fixture_text("cache_miss")),
        ),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 0
    assert result.argvs() == [
        ["tool", "run", "--offline", "--from", "ruff==1", "ruff"],
        ["tool", "run", "--from", "ruff==1", "ruff"],
    ]


def test_tool_online_warm_is_bounded(harness: Harness) -> None:
    """If the warming run also reports a miss, there is no third attempt."""
    rules = [
        rule(["tool", "run"], response(1, fixture_text("cache_miss"))),
    ]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 1
    assert len(result.argvs()) == 2


@pytest.mark.parametrize(
    "fixture", ["auth_missing_repo", "missing_package", "missing_revision"]
)
def test_tool_never_retries_other_failures(harness: Harness, fixture: str) -> None:
    """Auth and missing packages or revisions stop at the first attempt."""
    rules = [rule(["tool", "run"], response(1, fixture_text(fixture)))]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 1
    assert len(result.argvs()) == 1


def test_tool_online_step_can_be_forbidden(harness: Harness) -> None:
    """UV_GATE_ALLOW_ONLINE=0 also forbids warming a tool."""
    rules = [rule(["tool", "run"], response(1, fixture_text("cache_miss")))]
    result = harness.run(
        ["tool", "--from", "ruff==1", "--", "ruff"],
        rules,
        env={"UV_GATE_ALLOW_ONLINE": "0"},
    )
    assert result.status == 1
    assert len(result.argvs()) == 1


# environment, cache and refusals ---------------------------------------------


def test_uv_sees_a_cleaned_environment(harness: Harness) -> None:
    """Tokens, Git configuration and uv locations never reach uv."""
    lody = harness.home / ".lody"
    path = os.pathsep.join([str(harness.bin), str(lody / "bin"), "/usr/bin", "/bin"])
    result = harness.run(
        ["prepare"],
        [rule(OFFLINE_SYNC, response(0))],
        env={
            "GH_TOKEN": "secret",
            "GITHUB_TOKEN": "secret",
            "BASH_ENV": "/nonexistent",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.x.insteadOf",
            "UV_CACHE_DIR": "/elsewhere/.uv-cache",
            "UV_TOOL_DIR": "/elsewhere/.uv-tools",
        },
        path=path,
    )
    seen = result.uv_calls()[0]
    assert seen["env"]["GH_TOKEN"] is None
    assert seen["env"]["GITHUB_TOKEN"] is None
    assert seen["env"]["BASH_ENV"] is None
    assert seen["env"]["GIT_CONFIG_COUNT"] is None
    assert seen["env"]["GIT_CONFIG_KEY_0"] is None
    assert seen["env"]["UV_TOOL_DIR"] is None
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["UV_CACHE_DIR"] == str(harness.cache), "the global cache"
    assert str(lody) not in seen["path"]


def test_git_resolves_to_the_system_git(harness: Harness) -> None:
    """A shim directory first on PATH points `git` at /usr/bin/git."""
    if not Path("/usr/bin/git").exists():
        pytest.skip("no /usr/bin/git on this host")
    result = harness.run(["prepare"], [rule(OFFLINE_SYNC, response(0))])
    seen = result.uv_calls()[0]
    assert seen["git"] == str(Path("/usr/bin/git").resolve())
    shim = seen["path"].split(os.pathsep)[0]
    assert not Path(shim).exists(), "the shim directory is removed afterwards"


def test_same_device_leaves_link_mode_alone(harness: Harness) -> None:
    """With the cache beside the repository, UV_LINK_MODE is not forced."""
    result = harness.run(["prepare"], [rule(OFFLINE_SYNC, response(0))])
    assert result.uv_calls()[0]["env"]["UV_LINK_MODE"] is None


def test_cache_on_another_device_forces_copy_mode(
    harness: Harness, tmp_path: Path
) -> None:
    """A cache on a different filesystem selects UV_LINK_MODE=copy."""
    other = other_device_dir(tmp_path)
    if other is None:
        pytest.skip("no second filesystem available")
    cache = other / f"uv-gate-test-{tmp_path.name}"
    cache.mkdir()
    try:
        result = harness.run(
            ["prepare"], [rule(OFFLINE_SYNC, response(0))], cache=cache
        )
    finally:
        cache.rmdir()
    assert result.uv_calls()[0]["env"]["UV_LINK_MODE"] == "copy"


def test_environment_on_another_device_forces_copy_mode(
    harness: Harness, tmp_path: Path
) -> None:
    """A `.venv` symlinked to another filesystem also selects copy mode."""
    other = other_device_dir(tmp_path)
    if other is None:
        pytest.skip("no second filesystem available")
    target = other / f"uv-gate-venv-{tmp_path.name}"
    target.mkdir()
    (harness.repo / ".venv").symlink_to(target)
    try:
        result = harness.run(["prepare"], [rule(OFFLINE_SYNC, response(0))])
    finally:
        (harness.repo / ".venv").unlink()
        target.rmdir()
    assert result.uv_calls()[0]["env"]["UV_LINK_MODE"] == "copy"


def test_uv_missing_from_the_cleaned_path_is_a_refusal(harness: Harness) -> None:
    """With no uv on PATH the helper says so and runs nothing."""
    result = harness.run(["prepare"], [], path="/nonexistent")
    assert result.status == 2
    assert "uv is not available on the cleaned PATH" in result.stderr
    assert result.uv_calls() == []


def test_uncreatable_cache_directory_is_a_refusal(
    harness: Harness, tmp_path: Path
) -> None:
    """A cache path beneath a regular file cannot be created."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    result = harness.run(
        ["prepare"], [rule(OFFLINE_SYNC, response(0))], cache=blocker / "cache"
    )
    assert result.status == 2
    assert "cannot create the uv cache directory" in result.stderr
    assert result.uv_calls() == []


def test_unknown_subcommand_and_no_arguments_are_refusals(harness: Harness) -> None:
    """Usage errors exit 2 without running uv."""
    for args in ([], ["bogus"]):
        result = harness.run(args, [])
        assert result.status == 2
        assert "usage:" in result.stderr
    assert result.uv_calls() == []
