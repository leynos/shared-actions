"""Black-box tests: run ``uv_gate.py`` against a scripted fake ``uv``.

Each failure class is exercised end to end. The recorded call list is the
evidence for the central rule: the helper makes one offline attempt, goes
online at most once, and only for a proven cache miss.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import pytest
from _harness import Harness, fixture_text, other_device_dir, response, rule

import uv_gate

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

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


ALL_FAILURES = [
    ("cache_miss", "cache-miss"),
    ("stale_offline_ambiguous", "offline-resolution"),
    ("stale_lock_online", "stale-lock"),
    ("missing_lock", "missing-lock"),
    ("missing_package", "missing-package"),
    ("missing_revision", "missing-revision"),
    ("auth_missing_repo", "auth"),
]


@pytest.mark.parametrize(("fixture", "label"), ALL_FAILURES)
def test_run_never_retries_whatever_the_failure(
    harness: Harness, fixture: str, label: str
) -> None:
    """Whatever uv says, `run` makes one call, keeps the status and names the class."""
    result = harness.run(
        ["run", "--", "pytest"], [rule(["run"], response(7, fixture_text(fixture)))]
    )
    assert result.status == 7
    assert len(result.argvs()) == 1
    assert f"uv-gate: {label}:" in result.stderr


def test_run_reports_nothing_for_an_unrecognised_failure(harness: Harness) -> None:
    """An unrecognised failure is one call, uv's status and no class line."""
    result = harness.run(
        ["run", "--", "pytest"], [rule(["run"], response(5, "tests failed"))]
    )
    assert result.status == 5
    assert len(result.argvs()) == 1
    assert "uv-gate: unknown" not in result.stderr


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
    ("fixture", "label"),
    [
        pair
        for pair in ALL_FAILURES
        if pair[0] not in {"cache_miss", "stale_offline_ambiguous"}
    ],
)
def test_tool_never_retries_other_failures(
    harness: Harness, fixture: str, label: str
) -> None:
    """Every class other than a miss stops at the first attempt and is named."""
    rules = [rule(["tool", "run"], response(9, fixture_text(fixture)))]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 9
    assert len(result.argvs()) == 1
    assert f"uv-gate: {label}:" in result.stderr


def test_tool_does_not_retry_an_unrecognised_failure(harness: Harness) -> None:
    """Output that matches no signature is one attempt with uv's own status."""
    rules = [rule(["tool", "run"], response(4, "something odd"))]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 4
    assert len(result.argvs()) == 1


def test_tool_warms_online_once_on_offline_resolution(harness: Harness) -> None:
    """An ambiguous offline resolution failure is retried once, then named."""
    rules = [
        rule(
            ["tool", "run", "--offline"],
            response(1, fixture_text("stale_offline_ambiguous")),
        ),
        rule(
            ["tool", "run"],
            response(1, fixture_text("stale_lock_online")),
            without=("--offline",),
        ),
    ]
    result = harness.run(["tool", "--from", "ruff==1", "--", "ruff"], rules)
    assert result.status == 1
    assert len(result.argvs()) == 2
    assert "uv-gate: stale-lock:" in result.stderr


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
            "PATH": path,
        },
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
            ["prepare"],
            [rule(OFFLINE_SYNC, response(0))],
            env={"FAKE_UV_CACHE": str(cache)},
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
    result = harness.run(["prepare"], [], env={"PATH": "/nonexistent"})
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
        ["prepare"],
        [rule(OFFLINE_SYNC, response(0))],
        env={"FAKE_UV_CACHE": str(blocker / "cache")},
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


class _Devices:
    """Scripted device numbers for `build_context`, keyed by resolved path."""

    def __init__(self, numbers: dict[Path, int], default: int = 1) -> None:
        """Map resolved paths to device numbers."""
        self.numbers = {path.resolve(): number for path, number in numbers.items()}
        self.default = default

    def __call__(self, path: Path) -> int:
        """Return the scripted device for ``path``."""
        return self.numbers.get(path, self.default)


def _context_env(harness: Harness, devices: _Devices, environ: dict[str, str]) -> dict:
    """Build a context with injected devices and return the env uv would get."""
    full = {
        "PATH": f"{harness.bin}{os.pathsep}/usr/bin:/bin",
        "HOME": str(harness.home),
        "FAKE_UV_LOG": str(harness.log),
        "FAKE_UV_SCENARIO": str(harness.scenario),
        "FAKE_UV_CACHE": str(harness.cache),
        **environ,
    }
    harness.scenario.write_text("[]", encoding="utf-8")
    context = uv_gate.build_context(full, harness.repo, harness.home, devices)
    try:
        return dict(context.env)
    finally:
        context.close()


def test_copy_mode_is_set_end_to_end_when_devices_differ(harness: Harness) -> None:
    """With the cache on another device, the exported environment says copy."""
    devices = _Devices({harness.cache: 2})
    assert _context_env(harness, devices, {})["UV_LINK_MODE"] == "copy"


def test_copy_mode_is_absent_end_to_end_on_one_device(harness: Harness) -> None:
    """With one device for cache and repository, uv is left to link."""
    devices = _Devices({harness.cache: 5, harness.repo: 5})
    assert "UV_LINK_MODE" not in _context_env(harness, devices, {})


def test_project_environment_override_is_the_path_compared(harness: Harness) -> None:
    """UV_PROJECT_ENVIRONMENT, not `.venv`, decides the device comparison."""
    environment = harness.root / "custom-venv"
    environment.mkdir()
    devices = _Devices({environment: 9, harness.cache: 1, harness.repo: 1})
    env = _context_env(harness, devices, {"UV_PROJECT_ENVIRONMENT": str(environment)})
    assert env["UV_LINK_MODE"] == "copy"


def test_a_failing_device_query_is_a_refusal_not_a_traceback(harness: Harness) -> None:
    """An OSError from the device comparison becomes a GateError."""

    def failing(_path: Path) -> int:
        message = "stat failed"
        raise PermissionError(message)

    harness.scenario.write_text("[]", encoding="utf-8")
    environ = {
        "PATH": f"{harness.bin}{os.pathsep}/usr/bin:/bin",
        "HOME": str(harness.home),
        "FAKE_UV_LOG": str(harness.log),
        "FAKE_UV_SCENARIO": str(harness.scenario),
        "FAKE_UV_CACHE": str(harness.cache),
    }
    with pytest.raises(uv_gate.GateError, match="cannot compare the filesystems"):
        uv_gate.build_context(environ, harness.repo, harness.home, failing)


@pytest.mark.parametrize(
    "flag",
    [
        "--refresh-package=ruff",
        "--upgrade-package=ruff",
        "--reinstall-package=ruff",
        "-U",
        "-P",
        "-n",
    ],
)
def test_flag_families_and_short_aliases_are_refused(
    harness: Harness, flag: str
) -> None:
    """The whole refresh, upgrade and reinstall families, and -U/-P/-n, are refused."""
    for args in (["prepare", flag], ["tool", flag, "--", "ruff==1"]):
        result = harness.run(args, [rule(["sync"], response(0))])
        assert result.status == 2, args
        assert result.argvs() == []


def test_inherited_offline_and_no_cache_never_reach_uv(harness: Harness) -> None:
    """UV_OFFLINE and UV_NO_CACHE cannot defeat the online step or the cache."""
    result = harness.run(
        ["prepare"],
        [rule(OFFLINE_SYNC, response(0))],
        env={"UV_OFFLINE": "1", "UV_NO_CACHE": "1", "UV_REFRESH": "1"},
    )
    seen = result.uv_calls()[0]["env"]
    assert seen["UV_OFFLINE"] is None
    assert seen["UV_NO_CACHE"] is None
    assert seen["UV_REFRESH"] is None


def test_a_git_url_never_reaches_the_logs(harness: Harness) -> None:
    """Userinfo in a pinned Git URL is not echoed, warmed or refused."""
    url = "git+https://user:s3cret@example.invalid/org/tool.git"
    refused = harness.run(["tool", "--from", f"{url}@v1", "--", "tool"], [])
    assert refused.status == 2
    assert "s3cret" not in refused.stderr
    sha = "0123456789abcdef" * 2 + "01234567"
    rules = [
        rule(["tool", "run", "--offline"], response(1, fixture_text("cache_miss"))),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    warmed = harness.run(["tool", "--from", f"{url}@{sha}", "--", "tool"], rules)
    assert warmed.status == 0
    assert "s3cret" not in warmed.stderr
    assert "warming a git+ URL online once" in warmed.stderr


def test_the_stable_output_matches_its_snapshot(
    harness: Harness, snapshot: SnapshotAssertion
) -> None:
    """The helper's own ``uv-gate:`` lines are a stable contract; uv's are not."""
    miss = [
        rule(["tool", "run", "--offline"], response(1, fixture_text("cache_miss"))),
        rule(["tool", "run"], response(0), without=("--offline",)),
    ]
    cases = {
        "unknown subcommand": (["bogus"], []),
        "forbidden flag": (["prepare", "--upgrade-package=x"], []),
        "unpinned tool": (["tool", "--", "ruff"], []),
        "warmed tool": (["tool", "--from", "ruff==1", "--", "ruff"], miss),
        "prepare ok": (["prepare"], [rule(OFFLINE_SYNC, response(0))]),
        "run failed": (["run", "--", "pytest"], [rule(["run"], response(1, ""))]),
    }
    output = {
        name: [
            line
            for line in harness.run(args, rules).stderr.splitlines()
            if line.startswith("uv-gate:")
        ]
        for name, (args, rules) in cases.items()
    }
    assert output == snapshot


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["prepare", "--upgrade"], "--upgrade is not allowed"),
        (["run", "--refresh"], "--refresh is not allowed"),
        (["run"], "run needs a command"),
        (["tool", "--", "ruff"], "not pinned"),
        (["tool"], "tool needs"),
    ],
)
def test_bad_requests_are_refused_before_uv_is_looked_up(
    harness: Harness, args: list[str], message: str
) -> None:
    """Validation runs first: with no uv on PATH the refusal is still the request's."""
    result = harness.run(args, [], env={"PATH": "/nonexistent"})
    assert result.status == 2
    assert message in result.stderr
    assert "uv is not available" not in result.stderr


def test_a_lookup_failure_for_uv_is_a_refusal(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError while looking for uv becomes a GateError, not a traceback."""

    def fail(*_args: object, **_kwargs: object) -> str:
        message = "denied"
        raise PermissionError(message)

    monkeypatch.setattr(uv_gate.shutil, "which", fail)
    with pytest.raises(uv_gate.GateError, match="cannot look for uv"):
        uv_gate.build_context({"PATH": "/usr/bin"}, harness.repo, harness.home)


def test_resolving_the_uv_path_can_fail_cleanly(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv whose real path cannot be resolved is refused as well."""

    def fail(_self: Path, *_args: object, **_kwargs: object) -> Path:
        message = "loop"
        raise OSError(message)

    monkeypatch.setattr(type(harness.root), "resolve", fail)
    environ = {"PATH": f"{harness.bin}{os.pathsep}/usr/bin:/bin"}
    with pytest.raises(uv_gate.GateError, match="cannot look for uv"):
        uv_gate.build_context(environ, harness.repo, harness.home)
