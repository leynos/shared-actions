"""Unit tests for the pure parts of ``uv_gate``: classification, pins, cleaning."""

from __future__ import annotations

import os
import typing as typ

import pytest
from _harness import fixture_text, other_device_dir

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
    assert not uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path)


def test_needs_copy_mode_true_across_devices(tmp_path: Path) -> None:
    """A cache on another filesystem forces copy mode."""
    other = other_device_dir(tmp_path)
    if other is None:
        pytest.skip("no second filesystem available")
    cache = other / "uv-gate-unit-cache"
    cache.mkdir(exist_ok=True)
    try:
        assert uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path)
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
        assert uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path)
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
    assert uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path, devices)


def test_no_copy_mode_when_the_devices_match(tmp_path: Path) -> None:
    """The same device number leaves uv to link, without a second filesystem."""
    cache = tmp_path / "cache"
    cache.mkdir()
    devices = FakeDevices({cache: 7, tmp_path: 7})
    assert not uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path, devices)


def test_device_check_uses_the_repository_when_there_is_no_environment(
    tmp_path: Path,
) -> None:
    """A project environment that does not exist yet falls back to the repo."""
    cache = tmp_path / "cache"
    cache.mkdir()
    devices = FakeDevices({tmp_path: 3, cache: 3})
    assert not uv_gate.needs_copy_mode(cache, tmp_path / ".venv", tmp_path, devices)
    assert devices.asked == [tmp_path.resolve(), cache.resolve()]


def test_device_check_uses_the_existing_environment(tmp_path: Path) -> None:
    """An existing environment, not the repository, is compared with the cache."""
    cache = tmp_path / "cache"
    cache.mkdir()
    environment = tmp_path / ".venv"
    environment.mkdir()
    devices = FakeDevices({environment: 5, cache: 5, tmp_path: 9})
    assert not uv_gate.needs_copy_mode(cache, environment, tmp_path, devices)
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
    assert uv_gate.needs_copy_mode(cache, environment, tmp_path, devices)
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
    assert uv_gate.needs_copy_mode(link, tmp_path / ".venv", tmp_path, devices)
    assert real.resolve() in devices.asked
