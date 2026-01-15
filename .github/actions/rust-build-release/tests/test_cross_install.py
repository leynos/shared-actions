"""Tests for cross installation and upgrade logic."""

from __future__ import annotations

import hashlib
import io
import subprocess
import typing as typ
import zipfile

import pytest
from shared_actions_conftest import (
    CMD_MOX_UNSUPPORTED,
    _register_cross_version_stub,
    _register_rustup_toolchain_stub,
)

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from shared_actions_conftest import CmdMox

    from .conftest import HarnessFactory


@CMD_MOX_UNSUPPORTED
def test_installs_cross_when_missing(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Installs cross when it is missing."""
    harness = module_harness(cross_module)
    cross_path = _register_cross_version_stub(cmd_mox)
    cross_checks = [None, cross_path]

    def fake_which(name: str) -> str | None:
        if name == "cross":
            return cross_checks.pop(0) if cross_checks else cross_path
        return None

    harness.patch_shutil_which(fake_which)

    cmd_mox.replay()
    path, ver = cross_module.ensure_cross("0.2.5")
    cmd_mox.verify()

    assert path == cross_path
    assert ver == "0.2.5"
    install = next(
        cmd for cmd in harness.calls if cmd[:3] == ["cargo", "install", "cross"]
    )
    assert "--locked" in install
    idx = install.index("--version")
    assert install[idx + 1] == "0.2.5"
    # Prove we did not take the git fallback path
    assert "--git" not in install
    assert "--tag" not in install


def test_cross_install_failure_non_windows(
    cross_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Raises an error if cross installation fails on non-Windows hosts."""
    harness = module_harness(cross_module)
    harness.patch_platform("linux")

    def fake_which(name: str) -> str | None:
        return None if name == "cross" else "/usr/bin/rustup"

    def fail_install(cmd: list[str]) -> None:
        raise cross_module.ProcessExecutionError(cmd, 1, "", "install failed")

    harness.patch_shutil_which(fake_which)
    harness.patch_run_cmd(fail_install)

    with pytest.raises(cross_module.ProcessExecutionError) as exc_info:
        cross_module.ensure_cross("0.2.5")

    assert exc_info.value.retcode == 1
    assert exc_info.value.stderr == "install failed"


@CMD_MOX_UNSUPPORTED
def test_upgrades_outdated_cross(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Upgrades cross when an older version is installed."""
    harness = module_harness(cross_module)

    cross_path = _register_cross_version_stub(
        cmd_mox, ["cross 0.2.4\n", "cross 0.2.5\n"]
    )

    def fake_which(name: str) -> str | None:
        return cross_path if name == "cross" else None

    harness.patch_shutil_which(fake_which)

    cmd_mox.replay()
    path, ver = cross_module.ensure_cross("0.2.5")
    cmd_mox.verify()

    assert path == cross_path
    assert ver == "0.2.5"
    install = next(
        cmd for cmd in harness.calls if cmd[:3] == ["cargo", "install", "cross"]
    )
    assert "--locked" in install
    idx = install.index("--version")
    assert install[idx + 1] == "0.2.5"
    # Ensure upgrade used crates.io, not the git fallback
    assert "--git" not in install
    assert "--tag" not in install


@CMD_MOX_UNSUPPORTED
def test_uses_cached_cross(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Uses cached cross when version is sufficient."""
    harness = module_harness(cross_module)
    cross_path = _register_cross_version_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        return cross_path if name == "cross" else None

    harness.patch_shutil_which(fake_which)

    cmd_mox.replay()
    path, ver = cross_module.ensure_cross("0.2.5")
    cmd_mox.verify()

    assert path == cross_path
    assert ver == "0.2.5"
    assert not harness.calls


@CMD_MOX_UNSUPPORTED
def test_installs_prebuilt_cross_on_windows(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Uses the prebuilt cross binary on Windows hosts."""
    harness = module_harness(cross_module)
    cross_path = _register_cross_version_stub(cmd_mox)
    cross_checks = [None, cross_path]

    def fake_which(name: str) -> str | None:
        if name == "cross":
            return cross_checks.pop(0) if cross_checks else cross_path
        return None

    harness.patch_shutil_which(fake_which)
    harness.patch_platform("win32")

    release_call_args: list[str] = []

    def fake_release(version: str) -> bool:
        release_call_args.append(version)
        return True

    harness.patch_attr("install_cross_release", fake_release)

    cmd_mox.replay()
    path, ver = cross_module.ensure_cross("0.2.5")
    cmd_mox.verify()

    assert release_call_args == ["0.2.5"]
    assert path == cross_path
    assert ver == "0.2.5"
    assert all(cmd[:2] != ["cargo", "install"] for cmd in harness.calls)


def test_install_cross_release_validates_binary(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
    tmp_path: Path,
) -> None:
    """Cross release installer verifies the downloaded binary executes."""
    harness = module_harness(cross_module)
    module = cross_module

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("cross.exe", b"stub")
    payload = archive_bytes.getvalue()
    payload_hash = hashlib.sha256(payload).hexdigest()

    class FakeBinaryResponse:
        def __enter__(self) -> FakeBinaryResponse:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            return False

        def read(self) -> bytes:
            return payload

    class FakeTextResponse:
        def __enter__(self) -> FakeTextResponse:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            return False

        def read(self) -> bytes:
            return f"{payload_hash}  cross-x86_64-pc-windows-msvc.zip".encode()

    def fake_urlopen(url: str) -> FakeBinaryResponse | FakeTextResponse:
        assert url.startswith("https://github.com/cross-rs/cross/releases/download/")
        return FakeTextResponse() if url.endswith(".sha256") else FakeBinaryResponse()

    temp_dir = tmp_path / "tmp"

    class FakeTempDir:
        def __enter__(self) -> str:
            temp_dir.mkdir(parents=True, exist_ok=True)
            return str(temp_dir)

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            return False

    run_calls: list[list[str]] = []

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        capture_output: bool = False,
        check: bool = False,
        text: bool = False,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = allowed_names
        cmd = [executable, *args]
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="cross 0.2.5\n")

    messages = echo_recorder(module)

    home_dir = tmp_path / "home"

    harness.monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    harness.monkeypatch.setattr(
        module.tempfile, "TemporaryDirectory", lambda: FakeTempDir()
    )
    harness.monkeypatch.setattr(module, "run_validated", fake_run)
    harness.monkeypatch.setattr(module.Path, "home", lambda: home_dir)

    assert module.install_cross_release("0.2.5") is True

    installed_path = home_dir / ".cargo" / "bin" / "cross.exe"
    assert installed_path.exists()
    assert run_calls
    last_cmd = run_calls[-1]
    assert last_cmd[0].endswith("cross.exe")
    assert last_cmd[1:] == ["--version"]
    assert any("Installed cross binary reports" in msg for msg, _ in messages)


def test_install_cross_release_rejects_hash_mismatch(
    cross_module: ModuleType, module_harness: HarnessFactory, tmp_path: Path
) -> None:
    """Release installer aborts when the downloaded hash mismatches."""
    harness = module_harness(cross_module)

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("cross.exe", b"stub")
    payload = archive_bytes.getvalue()
    good_hash = hashlib.sha256(payload).hexdigest()
    bad_hash = (int(good_hash, 16) ^ 1).to_bytes(32, "big").hex()

    class FakeBinaryResponse:
        def __enter__(self) -> FakeBinaryResponse:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            return False

        def read(self) -> bytes:
            return payload

    class FakeTextResponse:
        def __init__(self, hash_value: str) -> None:
            self.hash_value = hash_value

        def __enter__(self) -> FakeTextResponse:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            return False

        def read(self) -> bytes:
            return f"{self.hash_value}  cross-x86_64-pc-windows-msvc.zip".encode()

    responses = {
        "archive": FakeBinaryResponse(),
        "hash": FakeTextResponse(bad_hash),
    }

    temp_dir = tmp_path / "tmp"
    home_dir = tmp_path / "home"

    class FakeTempDir:
        def __enter__(self) -> str:
            temp_dir.mkdir(parents=True, exist_ok=True)
            return str(temp_dir)

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            return False

    def fake_urlopen(url: str) -> FakeBinaryResponse | FakeTextResponse:
        assert url.startswith("https://github.com/cross-rs/cross/releases/download/")
        return responses["hash" if url.endswith(".sha256") else "archive"]

    harness.monkeypatch.setattr(cross_module.urllib.request, "urlopen", fake_urlopen)
    harness.monkeypatch.setattr(
        cross_module.tempfile, "TemporaryDirectory", lambda: FakeTempDir()
    )
    harness.monkeypatch.setattr(cross_module.Path, "home", lambda: home_dir)

    assert cross_module.install_cross_release("0.2.5") is False


@CMD_MOX_UNSUPPORTED
def test_installs_cross_without_container_runtime(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Installs cross even when no container runtime is available."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    cross_checks = [None, cross_path]

    def fake_which(name: str) -> str | None:
        if name == "cross":
            return cross_checks.pop(0) if cross_checks else cross_path
        if name in {"docker", "podman"}:
            return None
        return rustup_path if name == "rustup" else None

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    cross_env.patch_run_cmd()
    app_env.patch_run_cmd()

    cmd_mox.replay()
    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)
    cmd_mox.verify()

    install = next(
        cmd for cmd in cross_env.calls if cmd[:3] == ["cargo", "install", "cross"]
    )
    assert "--locked" in install
    idx = install.index("--version")
    assert install[idx + 1] == "0.2.5"
    build_cmd = app_env.calls[-1]
    assert build_cmd[0] == "cargo"
    assert build_cmd[1] == f"+{default_toolchain}-x86_64-unknown-linux-gnu"
    # Ensure no container runtime calls were attempted
    assert all(cmd[0] not in {"docker", "podman"} for cmd in app_env.calls)
    assert all(cmd[0] not in {"docker", "podman"} for cmd in cross_env.calls)


@CMD_MOX_UNSUPPORTED
def test_falls_back_to_git_when_crates_io_unavailable(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Falls back to git install when crates.io is unavailable."""
    harness = module_harness(cross_module)
    cross_path = _register_cross_version_stub(cmd_mox)
    cross_checks = [None, cross_path]

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if len(harness.calls) == 1:
            raise cross_module.ProcessExecutionError(cmd, 1, "", "")
        return

    def fake_which(name: str) -> str | None:
        if name == "cross":
            return cross_checks.pop(0) if cross_checks else cross_path
        return None

    harness.patch_run_cmd(run_cmd_side_effect)
    harness.patch_shutil_which(fake_which)

    cmd_mox.replay()
    path, ver = cross_module.ensure_cross("0.2.5")
    cmd_mox.verify()

    assert len(harness.calls) == 2
    first, second = harness.calls
    # First attempt was crates.io
    assert "--git" not in first
    assert "--tag" not in first
    # Second attempt is the git fallback with a tag
    assert "--git" in second
    assert "--tag" in second
    assert "v0.2.5" in second
    assert path == cross_path
    assert ver == "0.2.5"


def test_falls_back_to_cargo_when_runtime_unusable(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
) -> None:
    """Falls back to cargo when docker exists but is unusable."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    docker_path = "/usr/bin/docker"
    cross_path = "/usr/bin/cross"
    rustup_path = "/usr/bin/rustup"

    def fake_which(name: str) -> str | None:
        if name == "docker":
            return docker_path
        if name == "cross":
            return cross_path
        return rustup_path if name == "rustup" else None

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        capture_output: bool = False,
        check: bool = False,
        text: bool = False,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = allowed_names
        cmd = [executable, *args]
        if executable == docker_path:
            return subprocess.CompletedProcess(cmd, 1, stdout="")
        if len(cmd) > 1 and cmd[1] == "toolchain":
            output = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=output)
        return subprocess.CompletedProcess(cmd, 0, stdout="cross 0.2.5\n")

    cross_env.patch_subprocess_run(fake_run)
    app_env.patch_subprocess_run(fake_run)

    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)

    assert any(cmd[0] == "cargo" for cmd in app_env.calls)
    assert all(cmd[0] != "cross" for cmd in app_env.calls)


def test_returns_none_when_install_fails_on_windows(
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns None when cross installation fails on Windows."""
    harness = module_harness(cross_module)

    def failing_run_cmd(cmd: list[str]) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    harness.patch_run_cmd(failing_run_cmd)
    harness.patch_attr("install_cross_release", lambda _: False)
    harness.patch_shutil_which(lambda name: None)
    harness.patch_platform("win32")

    path, ver = cross_module.ensure_cross("0.2.5")

    assert len(harness.calls) == 2
    assert path is None
    assert ver is None
    io = capsys.readouterr()
    msg = io.err.lower()
    assert "warning" in msg
    assert "cross install failed; continuing without cross" in msg
