"""Unit tests for the pure parts of ``uv_gate``: classification, pins, cleaning."""

from __future__ import annotations

import os
import pathlib
import stat
import typing as typ

import pytest
from _harness import fixture_text, other_device_dir
from hypothesis import given
from hypothesis import strategies as st

import uv_gate

if typ.TYPE_CHECKING:
    from pathlib import Path

SEP = os.pathsep


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("cache_miss", uv_gate.Kind.CACHE_MISS),
        ("stale_offline_ambiguous", uv_gate.Kind.OFFLINE_RESOLUTION),
        ("stale_lock_online", uv_gate.Kind.STALE_LOCK),
        ("missing_lock", uv_gate.Kind.MISSING_LOCK),
        ("missing_package", uv_gate.Kind.MISSING_PACKAGE),
        ("missing_revision", uv_gate.Kind.MISSING_REVISION),
        ("auth_missing_repo", uv_gate.Kind.AUTH),
    ],
)
def test_classify_recognises_recorded_uv_output(
    fixture: str, kind: uv_gate.Kind
) -> None:
    """Each class is recognized from output recorded from uv 0.9.21."""
    assert uv_gate.classify(fixture_text(fixture)) is kind


def test_classify_tolerates_wrapped_messages() -> None:
    """Match a signature that spans lines, since uv wraps at the terminal width."""
    wrapped = (
        "Network connectivity is disabled, but\n      the   requested data\n"
        "  wasn't found in the cache for: `https://example.invalid/x.whl`"
    )
    assert uv_gate.classify(wrapped) is uv_gate.Kind.CACHE_MISS


@pytest.mark.parametrize(
    "text",
    [
        "",
        "AssertionError: 1 != 2",
        "error: Failed to spawn: `pytest`",
        "Network connectivity is disabled",
    ],
)
def test_classify_returns_unknown_for_everything_else(text: str) -> None:
    """Text that is not a recognized uv failure never triggers a retry path."""
    assert uv_gate.classify(text) is uv_gate.Kind.UNKNOWN


@pytest.mark.parametrize(
    "spec",
    [
        "ruff==0.16.4",
        "ty==0.0.1a20",
        "typos@1.2.3",
        "pkg[extra]==2.0",
        "git+https://github.com/leynos/tool.git@" + "a" * 40,
        "git+https://github.com/leynos/tool@" + "0123456789abcdef" * 2 + "01234567",
    ],
)
def test_is_pinned_accepts_exact_pins(spec: str) -> None:
    """Exact versions and full commit SHAs are accepted."""
    assert uv_gate.is_pinned(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "ruff",
        "ruff>=0.16",
        "ruff~=0.16.0",
        "ruff==",
        "typos@latest",
        "typos@",
        "==1.2.3",
        "@1.2.3",
        "-ruff==0.16.4",
        "ru ff==0.16.4",
        "git+https://github.com/leynos/tool.git@v0.1.3",
        "git+https://github.com/leynos/tool.git@" + "a" * 39,
        "git+https://github.com/leynos/tool.git",
        "",
    ],
)
def test_is_pinned_rejects_everything_else(spec: str) -> None:
    """Bare names, ranges, tags, branches and short SHAs are not pins."""
    assert not uv_gate.is_pinned(spec)


def test_clean_environment_drops_inherited_state(tmp_path: Path) -> None:
    """Tokens, Git configuration, BASH_ENV and uv locations are removed."""
    lody = tmp_path / ".lody"
    environ = {
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "y",
        "BASH_ENV": "/tmp/rc",  # noqa: S108  # value is never used
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "k",
        "UV_CACHE_DIR": ".uv-cache",
        "UV_TOOL_DIR": ".uv-tools",
        "KEEP": "me",
        "PATH": SEP.join(["/usr/bin", str(lody), str(lody / "bin"), "/bin"]),
    }
    env = uv_gate.clean_environment(environ, tmp_path)
    assert env == {
        "KEEP": "me",
        "PATH": SEP.join(["/usr/bin", "/bin"]),
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert "GH_TOKEN" in environ, "the input mapping is not modified"


def test_clean_environment_keeps_lookalike_path_entries(tmp_path: Path) -> None:
    """Only ``~/.lody`` and its children go; a sibling with the prefix stays."""
    lookalike = str(tmp_path / ".lody-tools")
    env = uv_gate.clean_environment({"PATH": lookalike}, tmp_path)
    assert env["PATH"] == lookalike


def test_needs_copy_mode_false_on_one_device(tmp_path: Path) -> None:
    """A cache and a repository on one filesystem link rather than copy."""
    cache = tmp_path / "cache"
    cache.mkdir()
    assert not uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path)


def test_needs_copy_mode_true_across_devices(tmp_path: Path) -> None:
    """A cache on another filesystem forces copy mode."""
    other = other_device_dir(tmp_path)
    if other is None:
        pytest.skip("no second filesystem available")
    cache = other / "uv-gate-unit-cache"
    cache.mkdir(exist_ok=True)
    try:
        assert uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path)
    finally:
        cache.rmdir()


def test_needs_copy_mode_follows_a_symlinked_environment(tmp_path: Path) -> None:
    """The environment's own device counts, even when ``.venv`` is a symlink."""
    other = other_device_dir(tmp_path)
    if other is None:
        pytest.skip("no second filesystem available")
    target = other / "uv-gate-unit-venv"
    target.mkdir(exist_ok=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    (tmp_path / ".venv").symlink_to(target)
    try:
        assert uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path)
    finally:
        (tmp_path / ".venv").unlink()
        target.rmdir()


def test_tool_spec_prefers_from_over_the_command() -> None:
    """``--from`` names the package, in either spelling."""
    assert uv_gate.tool_spec(["--from", "ruff==1"], ["ruff"]) == "ruff==1"
    assert uv_gate.tool_spec(["--from=ruff==1"], ["ruff"]) == "ruff==1"
    assert uv_gate.tool_spec([], ["typos@1.2.3", "--version"]) == "typos@1.2.3"


def test_tool_spec_needs_something_to_run() -> None:
    """No ``--from`` and no command is a refusal."""
    with pytest.raises(uv_gate.GateError):
        uv_gate.tool_spec([], [])


class FakeDevices:
    """Return scripted device numbers and record which paths were asked about."""

    def __init__(self, devices: dict[Path, int], default: int = 1) -> None:
        """Map resolved paths to device numbers; others get ``default``."""
        self.devices = {path.resolve(): number for path, number in devices.items()}
        self.default = default
        self.asked: list[Path] = []

    def __call__(self, path: Path) -> int:
        """Record ``path`` and return its scripted device."""
        self.asked.append(path)
        return self.devices.get(path, self.default)


def test_copy_mode_when_the_devices_differ(tmp_path: Path) -> None:
    """Different device numbers select copy mode, without a second filesystem."""
    cache = tmp_path / "cache"
    cache.mkdir()
    devices = FakeDevices({cache: 2})
    assert uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path, devices)


def test_no_copy_mode_when_the_devices_match(tmp_path: Path) -> None:
    """The same device number leaves uv to link, without a second filesystem."""
    cache = tmp_path / "cache"
    cache.mkdir()
    devices = FakeDevices({cache: 7, tmp_path: 7})
    assert not uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path, devices)


def test_device_check_uses_the_repository_when_there_is_no_environment(
    tmp_path: Path,
) -> None:
    """A project environment that does not exist yet falls back to the repo."""
    cache = tmp_path / "cache"
    cache.mkdir()
    devices = FakeDevices({tmp_path: 3, cache: 3})
    assert not uv_gate.copy_mode_required(cache, tmp_path / ".venv", tmp_path, devices)
    assert devices.asked == [tmp_path.resolve(), cache.resolve()]


def test_device_check_uses_the_existing_environment(tmp_path: Path) -> None:
    """An existing environment, not the repository, is compared with the cache."""
    cache = tmp_path / "cache"
    cache.mkdir()
    environment = tmp_path / ".venv"
    environment.mkdir()
    devices = FakeDevices({environment: 5, cache: 5, tmp_path: 9})
    assert not uv_gate.copy_mode_required(cache, environment, tmp_path, devices)
    assert devices.asked[0] == environment.resolve()


def test_device_check_resolves_a_symlinked_environment(tmp_path: Path) -> None:
    """A ``.venv`` symlinked elsewhere is judged by where it really lives."""
    cache = tmp_path / "cache"
    cache.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    environment = tmp_path / ".venv"
    try:
        environment.symlink_to(target, target_is_directory=True)
    except OSError:  # pragma: no cover - symlinks need privilege on Windows
        pytest.skip("symlinks are not permitted here")
    devices = FakeDevices({target: 2, cache: 1, tmp_path: 1})
    assert uv_gate.copy_mode_required(cache, environment, tmp_path, devices)
    assert target.resolve() in devices.asked


def test_device_check_resolves_a_symlinked_cache(tmp_path: Path) -> None:
    """A cache reached through a symlink is judged by its real location."""
    real = tmp_path / "real-cache"
    real.mkdir()
    link = tmp_path / "cache-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:  # pragma: no cover - symlinks need privilege on Windows
        pytest.skip("symlinks are not permitted here")
    devices = FakeDevices({real: 4, tmp_path: 1})
    assert uv_gate.copy_mode_required(link, tmp_path / ".venv", tmp_path, devices)
    assert real.resolve() in devices.asked


@pytest.mark.parametrize(
    "variable",
    [
        "UV_OFFLINE",
        "UV_NO_CACHE",
        "UV_FROZEN",
        "UV_LOCKED",
        "UV_REFRESH",
        "UV_REFRESH_PACKAGE",
        "UV_UPGRADE",
    ],
)
def test_clean_environment_drops_policy_overrides(
    tmp_path: Path, variable: str
) -> None:
    """Inherited uv switches that override the gate's policy are removed."""
    env = uv_gate.clean_environment({variable: "1", "PATH": "/usr/bin"}, tmp_path)
    assert variable not in env


@pytest.mark.parametrize(
    "flag",
    [
        "--refresh",
        "--refresh-package",
        "--refresh-package=x",
        "--upgrade-package=x",
        "--reinstall-package",
        "-U",
        "-P",
        "-Pruff",
        "-n",
        "-qU",
        "-vn",
        "-qn",
        "-qPruff",
        "-vvU",
        "-Pruff==1.0",
        "-n4",
    ],
)
def test_refuse_forbidden_covers_the_families(flag: str) -> None:
    """Whole flag families and the short aliases are refused."""
    with pytest.raises(uv_gate.GateError):
        uv_gate._refuse_forbidden(["--group", "dev", flag])


@pytest.mark.parametrize(
    "flag", ["--group", "--python=3.12", "-p", "-q", "-qv", "-p3.12", "--extra"]
)
def test_refuse_forbidden_leaves_ordinary_flags_alone(flag: str) -> None:
    """Ordinary options are not caught by the families or the aliases."""
    uv_gate._refuse_forbidden([flag, "dev"])


@pytest.mark.parametrize(
    ("spec", "described"),
    [
        ("git+https://user:secret@host/x.git@v1", "a git+ URL"),
        ("ruff>=0.16", "ruff"),
        ("typos@latest", "typos"),
        ("==1", "an unnamed tool"),
    ],
)
def test_describe_spec_names_the_tool_only(spec: str, described: str) -> None:
    """Logs carry the package name, never a URL or its credentials."""
    assert uv_gate.describe_spec(spec) == described


NAMES = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,20}", fullmatch=True)
VERSIONS = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,20}", fullmatch=True).filter(
    lambda v: v != "latest"
)
EXTRAS = st.one_of(st.just(""), st.from_regex(r"\[[a-z0-9,]{1,8}\]", fullmatch=True))
SHAS = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)


@given(NAMES, EXTRAS, VERSIONS, st.sampled_from(["==", "@"]))
def test_every_well_formed_exact_pin_is_accepted(
    name: str, extras: str, version: str, separator: str
) -> None:
    """Any valid name, optional extras, separator and version is a pin."""
    assert uv_gate.is_pinned(f"{name}{extras}{separator}{version}")


@given(SHAS)
def test_every_full_lowercase_sha_is_a_pin(sha: str) -> None:
    """A 40-digit lower-case SHA pins a git URL, with or without a fragment."""
    assert uv_gate.is_pinned(f"git+https://example.invalid/o/r.git@{sha}")
    assert uv_gate.is_pinned(
        f"git+https://example.invalid/o/r.git@{sha}#subdirectory=x"
    )


@given(NAMES, st.sampled_from([">=", "<=", "~=", ">", "<", "!="]), VERSIONS)
def test_ranges_are_never_pins(name: str, operator: str, version: str) -> None:
    """Range specifiers are not exact pins."""
    assert not uv_gate.is_pinned(f"{name}{operator}{version}")


@given(
    st.from_regex(r"[0-9a-f]{0,39}|[0-9A-F]{40}|[0-9a-f]{41}", fullmatch=True).filter(
        lambda sha: not (len(sha) == 40 and sha == sha.lower())
    )
)
def test_short_long_or_upper_case_shas_are_not_pins(sha: str) -> None:
    """Only a full 40-digit lower-case SHA pins a Git reference."""
    assert not uv_gate.is_pinned(f"git+https://example.invalid/o/r.git@{sha}")


@given(NAMES, st.sampled_from(["==", "@"]))
def test_latest_and_empty_versions_are_never_pins(name: str, separator: str) -> None:
    """``latest`` and an empty version are not exact versions."""
    assert not uv_gate.is_pinned(f"{name}{separator}latest")
    assert not uv_gate.is_pinned(f"{name}{separator}")


def test_validate_request_is_pure_and_accepts_good_requests() -> None:
    """Good requests pass without any environment or filesystem access."""
    uv_gate.validate_request("prepare", ["--group", "dev"])
    uv_gate.validate_request("run", ["--group", "dev", "--", "pytest"])
    uv_gate.validate_request("tool", ["--from", "ruff==1", "--", "ruff"])


@pytest.mark.parametrize(
    ("subcommand", "rest"),
    [
        ("prepare", ["-U"]),
        ("run", []),
        ("run", ["--refresh-package", "x", "--", "pytest"]),
        ("tool", ["--", "ruff"]),
        ("tool", ["-n", "--from", "ruff==1", "--", "ruff"]),
    ],
)
def test_validate_request_refuses_bad_requests(
    subcommand: str, rest: list[str]
) -> None:
    """Forbidden flags, missing commands and unpinned tools are all refused."""
    with pytest.raises(uv_gate.GateError):
        uv_gate.validate_request(subcommand, rest)


def test_flags_after_the_separator_belong_to_the_command() -> None:
    """Only options before ``--`` are uv's; the command's own flags are free."""
    uv_gate.validate_request("run", ["--", "pytest", "--upgrade", "-U", "-n", "4"])


def test_prepare_checks_every_argument_it_forwards() -> None:
    """`prepare` forwards everything to uv sync, so nothing escapes the check."""
    with pytest.raises(uv_gate.GateError):
        uv_gate.validate_request("prepare", ["--", "--upgrade"])


def test_needs_copy_mode_is_a_pure_comparison_of_two_devices(tmp_path: Path) -> None:
    """Given resolved paths, only the injected device function is consulted."""
    first, second = tmp_path / "a", tmp_path / "b"
    assert uv_gate.needs_copy_mode(first, second, FakeDevices({first: 1, second: 2}))
    assert not uv_gate.needs_copy_mode(
        first, second, FakeDevices({first: 4, second: 4})
    )


def test_needs_copy_mode_never_touches_the_filesystem() -> None:
    """Paths that do not exist are fine: nothing but ``device`` is called."""
    seen: list[pathlib.Path] = []

    def device(path: pathlib.Path) -> int:
        seen.append(path)
        return 1

    missing = pathlib.Path("/nonexistent/uv-gate/a")
    other = pathlib.Path("/nonexistent/uv-gate/b")
    assert not uv_gate.needs_copy_mode(missing, other, device)
    assert seen == [missing, other]


def test_probe_link_paths_turns_an_oserror_into_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that cannot be inspected is a GateError, not a traceback."""

    def fail(_self: Path) -> bool:
        message = "denied"
        raise PermissionError(message)

    monkeypatch.setattr(type(tmp_path), "exists", fail)
    with pytest.raises(uv_gate.GateError, match="cannot resolve"):
        uv_gate.probe_link_paths(tmp_path / "cache", tmp_path / ".venv", tmp_path)


def test_a_stalled_cache_query_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv that never answers `cache dir` ends in a GateError, not a hang."""
    fake = tmp_path / "uv"
    fake.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(uv_gate, "CACHE_QUERY_SECONDS", 0.3)
    with pytest.raises(uv_gate.GateError, match="did not answer"):
        uv_gate._query_cache_dir(str(fake), {"PATH": "/usr/bin:/bin"})


def test_a_failing_git_shim_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the shim directory cannot be made, the context is refused and closed."""

    def fail(*_args: object, **_kwargs: object) -> str:
        message = "no space"
        raise OSError(message)

    monkeypatch.setattr(uv_gate.tempfile, "mkdtemp", fail)
    context = uv_gate.Context({"PATH": "/usr/bin"}, "uv", tmp_path)
    if not (uv_gate.SYSTEM_GIT.is_file() and os.access(uv_gate.SYSTEM_GIT, os.X_OK)):
        pytest.skip("no system git to shim")
    with pytest.raises(uv_gate.GateError, match="Git shim"):
        uv_gate._with_git_shim(context)


def test_a_shim_that_fails_after_its_directory_exists_is_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the symlink cannot be made, the half-built shim directory is removed."""
    if not (uv_gate.SYSTEM_GIT.is_file() and os.access(uv_gate.SYSTEM_GIT, os.X_OK)):
        pytest.skip("no system git to shim")
    made = tmp_path / "shim"
    made.mkdir()
    monkeypatch.setattr(uv_gate.tempfile, "mkdtemp", lambda **_kw: str(made))

    def fail(_self: Path, _target: object) -> None:
        message = "no symlinks"
        raise OSError(message)

    monkeypatch.setattr(type(tmp_path), "symlink_to", fail)
    context = uv_gate.Context({"PATH": "/usr/bin"}, "uv", tmp_path)
    with pytest.raises(uv_gate.GateError, match="Git shim"):
        uv_gate._with_git_shim(context)
    assert not made.exists()
