"""Tests for the target installation helpers in the rust-build-release action."""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
from shared_actions_conftest import (
    CMD_MOX_UNSUPPORTED,
    _register_cross_version_stub,
    _register_docker_info_stub,
    _register_podman_info_stub,
    _register_rustup_toolchain_stub,
)

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from shared_actions_conftest import CmdMox

    from .conftest import HarnessFactory


@CMD_MOX_UNSUPPORTED
def test_skips_target_install_when_cross_available(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Continues when target addition fails but cross is available."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd[:3] == ["rustup", "target", "add"]:
            raise subprocess.CalledProcessError(1, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    cross_path = _register_cross_version_stub(cmd_mox)
    docker_path = _register_docker_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "cross": cross_path,
            "docker": docker_path,
            "rustup": rustup_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)

    cmd_mox.replay()

    main_module.main("aarch64-pc-windows-gnu", default_toolchain)
    cmd_mox.verify()
    build_cmd = app_env.calls[-1]
    assert build_cmd[0] == "cross"
    assert build_cmd[1] == f"+{default_toolchain}"


@CMD_MOX_UNSUPPORTED
def test_errors_when_target_unsupported_without_cross(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emits an error when the toolchain lacks the requested target."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)

    def fake_which(name: str) -> str | None:
        return rustup_path if name == "rustup" else None

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd[:3] == ["rustup", "target", "add"]:
            raise subprocess.CalledProcessError(1, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)
    app_env.patch_attr("ensure_cross", lambda *_: (None, None))
    app_env.patch_attr("runtime_available", lambda name: False)

    cmd_mox.replay()
    with pytest.raises(main_module.typer.Exit):
        main_module.main("thumbv7em-none-eabihf", default_toolchain)
    cmd_mox.verify()

    err = capsys.readouterr().err
    assert "does not support target 'thumbv7em-none-eabihf'" in err


@CMD_MOX_UNSUPPORTED
def test_falls_back_to_cargo_when_cross_container_fails(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Falls back to cargo when cross exits with a container error."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            raise subprocess.CalledProcessError(125, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    cross_path = _register_cross_version_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        return rustup_path if name == "rustup" else None

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)

    app_env.patch_attr("ensure_cross", lambda required: (cross_path, required))
    app_env.patch_attr("runtime_available", lambda name: True)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)
    cmd_mox.verify()
    build_cmd = app_env.calls[-1]
    assert build_cmd[0] == "cargo"
    assert build_cmd[1] == f"+{default_toolchain}-x86_64-unknown-linux-gnu"


@CMD_MOX_UNSUPPORTED
def test_falls_back_to_cargo_when_podman_unusable(
    main_module: ModuleType,
    cross_module: ModuleType,
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Fallback to cargo when podman runtime detection fails quickly (issue #97)."""
    cross_env = module_harness(cross_module)
    runtime_env = module_harness(runtime_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    podman_path = _register_podman_info_stub(cmd_mox, exit_code=1)

    def fake_which(name: str) -> str | None:
        if name == "podman":
            return podman_path
        if name == "cross":
            return cross_path
        return rustup_path if name == "rustup" else None

    cross_env.patch_shutil_which(fake_which)
    runtime_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)

    app_env.patch_attr("ensure_cross", lambda required: (cross_path, required))
    app_env.patch_attr("runtime_available", runtime_module.runtime_available)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)
    cmd_mox.verify()

    assert any(cmd[0] == "cargo" for cmd in app_env.calls)
    assert all(cmd[0] != "cross" for cmd in app_env.calls)


@pytest.mark.parametrize(
    "target",
    [
        "x86_64-pc-windows-msvc",
        "aarch64-pc-windows-gnu",
    ],
    ids=("x86_64-pc-windows-msvc", "aarch64-pc-windows-gnu"),
)
def test_windows_host_skips_container_probe_for_windows_targets(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    target: str,
) -> None:
    """Does not probe container runtimes for Windows targets on Windows hosts."""
    harness = module_harness(main_module)
    harness.patch_platform("win32")

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
        if executable == "/usr/bin/rustup" and args[:2] == ["toolchain", "list"]:
            stdout = f"{default_toolchain}-{target}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        if executable == "/usr/bin/rustup" and args[:2] == ["which", "rustc"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/rustc")
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    harness.patch_subprocess_run(fake_run)
    harness.patch_run_cmd(lambda _: None)

    def fake_which(name: str) -> str | None:
        return "/usr/bin/rustup" if name == "rustup" else None

    harness.patch_shutil_which(fake_which)
    runtime_calls: list[str] = []

    def fake_runtime(name: str, *, cwd: object | None = None) -> bool:
        runtime_calls.append(name)
        _ = cwd
        return False

    harness.patch_attr("runtime_available", fake_runtime)
    harness.patch_attr("ensure_cross", lambda *_: (None, None))

    main_module.main(target, default_toolchain)

    assert not runtime_calls


def test_windows_host_probes_container_for_non_windows_targets(
    main_module: ModuleType,
    module_harness: HarnessFactory,
) -> None:
    """Still probes container runtimes for non-Windows targets."""
    harness = module_harness(main_module)
    harness.patch_platform("win32")

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
        if executable == "/usr/bin/rustup" and args[:2] == ["toolchain", "list"]:
            stdout = f"{default_toolchain}-x86_64-pc-windows-msvc\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        if executable == "/usr/bin/rustup" and args[:2] == ["which", "rustc"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/rustc")
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    harness.patch_subprocess_run(fake_run)
    harness.patch_run_cmd(lambda _: None)

    def fake_which(name: str) -> str | None:
        return "/usr/bin/rustup" if name == "rustup" else None

    harness.patch_shutil_which(fake_which)
    runtime_calls: list[str] = []

    def fake_runtime(name: str, *, cwd: object | None = None) -> bool:
        runtime_calls.append(name)
        _ = cwd
        return False

    harness.patch_attr("runtime_available", fake_runtime)
    harness.patch_attr("ensure_cross", lambda *_: (None, None))

    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)

    assert set(runtime_calls) == {"docker", "podman"}


@pytest.mark.parametrize(
    ("host_platform", "target", "probe_outcome"),
    [
        ("win32", "x86_64-pc-windows-msvc", "skip"),
        ("win32", "aarch64-pc-windows-gnu", "skip"),
        ("win32", "x86_64-uwp-windows-msvc", "skip"),
        ("win32", "x86_64-pc-windows-gnullvm", "skip"),
        ("win32", "x86_64-unknown-linux-gnu", "probe"),
        ("linux", "x86_64-pc-windows-msvc", "probe"),
    ],
)
def test_should_probe_container_handles_windows_targets(
    main_module: ModuleType,
    host_platform: str,
    target: str,
    probe_outcome: typ.Literal["probe", "skip"],
) -> None:
    """Helper correctly decides when to probe container runtimes."""
    expected = probe_outcome == "probe"
    assert main_module.should_probe_container(host_platform, target) is expected


def test_probe_runtime_returns_runtime_available(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_probe_runtime returns the underlying runtime availability flag."""
    harness = module_harness(main_module)
    harness.patch_attr("runtime_available", lambda name: name == "docker")

    assert main_module._probe_runtime("docker") is True
    assert main_module._probe_runtime("podman") is False

    captured = capsys.readouterr()
    assert captured.err == ""


def test_probe_runtime_warns_on_timeout(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Timeouts are converted into warnings and treated as unavailable."""
    harness = module_harness(main_module)

    def raise_timeout(name: str) -> bool:
        raise subprocess.TimeoutExpired(cmd=f"{name} info", timeout=5)

    harness.patch_attr("runtime_available", raise_timeout)

    assert main_module._probe_runtime("podman") is False

    err = capsys.readouterr().err
    expected = (
        "::warning::podman runtime probe timed out after 5s; "
        "treating runtime as unavailable"
    )
    assert expected in err


def test_probe_runtime_propagates_unexpected_error(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Errors other than timeouts propagate to the caller."""
    harness = module_harness(main_module)

    class ProbeError(RuntimeError):
        """Sentinel error for runtime probe tests."""

    def raise_error(name: str) -> bool:
        raise ProbeError

    harness.patch_attr("runtime_available", raise_error)

    with pytest.raises(ProbeError):
        main_module._probe_runtime("docker")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_runtime_available_handles_timeout(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treat runtime probe timeouts as unavailable instead of crashing."""
    harness = module_harness(main_module)
    default_toolchain = main_module.DEFAULT_TOOLCHAIN

    harness.patch_shutil_which(
        lambda name: "/usr/bin/rustup" if name == "rustup" else None
    )

    def fake_run_validated(
        executable: str, args: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if executable == "/usr/bin/rustup" and args[:2] == ["toolchain", "list"]:
            stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
            return subprocess.CompletedProcess([executable, *args], 0, stdout=stdout)
        pytest.fail(f"unexpected run_validated call: {executable} {args}")

    harness.patch_attr("run_validated", fake_run_validated)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd[:3] == ["/usr/bin/rustup", "target", "add"]:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd and cmd[0] == "cargo":
            return
        pytest.fail(f"unexpected run_cmd call: {cmd}")

    harness.patch_run_cmd(run_cmd_side_effect)
    harness.patch_attr("configure_windows_linkers", lambda *_: None)

    def timeout_runtime(_name: str, *, cwd: object | None = None) -> bool:
        _ = cwd
        raise subprocess.TimeoutExpired(cmd="podman info", timeout=10)

    harness.patch_attr("runtime_available", timeout_runtime)
    harness.patch_attr("ensure_cross", lambda *_: (None, None))

    with pytest.raises(main_module.typer.Exit):
        main_module.main("thumbv7em-none-eabihf", default_toolchain)

    err = capsys.readouterr().err
    expected_docker = (
        "::warning::docker runtime probe timed out after 10s; "
        "treating runtime as unavailable"
    )
    expected_podman = (
        "::warning::podman runtime probe timed out after 10s; "
        "treating runtime as unavailable"
    )
    assert expected_docker in err
    assert expected_podman in err


def test_configure_windows_linkers_prefers_toolchain_gcc(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    tmp_path: Path,
) -> None:
    """Toolchain-provided GCC is preferred for Windows host builds."""
    harness = module_harness(main_module)
    harness.patch_platform("win32")
    toolchain_name = "1.89.0-x86_64-pc-windows-gnu"
    host_triple = "x86_64-pc-windows-gnu"
    rustup_path = "/usr/bin/rustup"

    toolchain_root = tmp_path / "toolchain"
    rustc_path = toolchain_root / "bin" / "rustc.exe"
    rustc_path.parent.mkdir(parents=True)
    rustc_path.write_text("", encoding="utf-8")
    host_linker = toolchain_root / "lib" / "rustlib" / host_triple / "bin" / "gcc.exe"
    host_linker.parent.mkdir(parents=True)
    host_linker.write_text("", encoding="utf-8")

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = allowed_names
        cmd = [executable, *args]
        assert cmd[:2] == [rustup_path, "which"]
        return subprocess.CompletedProcess(cmd, 0, stdout=str(rustc_path))

    harness.monkeypatch.setattr(main_module, "run_validated", fake_run)
    harness.monkeypatch.setattr(main_module.shutil, "which", lambda name: None)
    harness.monkeypatch.delenv(
        "CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER", raising=False
    )

    main_module.configure_windows_linkers(toolchain_name, host_triple, rustup_path)

    expected = str(host_linker)
    assert os.environ["CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER"] == expected


@pytest.mark.parametrize(
    "linker_name",
    [
        "aarch64-w64-mingw32-gcc",
        "aarch64-w64-mingw32-clang",
    ],
)
def test_configure_windows_linkers_sets_cross_linker(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    tmp_path: Path,
    linker_name: str,
) -> None:
    """Cross linkers discovered on PATH are exported for non-host targets."""
    harness = module_harness(main_module)
    harness.patch_platform("win32")
    toolchain_name = "1.89.0-x86_64-pc-windows-gnu"
    rustup_path = "/usr/bin/rustup"
    host_triple = "x86_64-pc-windows-gnu"

    toolchain_root = tmp_path / "toolchain"
    rustc_path = toolchain_root / "bin" / "rustc.exe"
    rustc_path.parent.mkdir(parents=True)
    rustc_path.write_text("", encoding="utf-8")
    host_linker = toolchain_root / "lib" / "rustlib" / host_triple / "bin" / "gcc.exe"
    host_linker.parent.mkdir(parents=True)
    host_linker.write_text("", encoding="utf-8")
    cross_linker = tmp_path / f"{linker_name}.exe"
    cross_linker.write_text("", encoding="utf-8")

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = allowed_names
        cmd = [executable, *args]
        assert cmd[:2] == [rustup_path, "which"]
        return subprocess.CompletedProcess(cmd, 0, stdout=str(rustc_path))

    harness.monkeypatch.setattr(main_module, "run_validated", fake_run)

    def fake_which(name: str) -> str | None:
        return str(cross_linker) if name == linker_name else None

    harness.monkeypatch.setattr(main_module.shutil, "which", fake_which)
    harness.monkeypatch.delenv(
        "CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER", raising=False
    )
    harness.monkeypatch.delenv(
        "CARGO_TARGET_AARCH64_PC_WINDOWS_GNU_LINKER", raising=False
    )

    main_module.configure_windows_linkers(toolchain_name, host_triple, rustup_path)

    host_env = os.environ["CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER"]
    cross_env = os.environ["CARGO_TARGET_AARCH64_PC_WINDOWS_GNU_LINKER"]
    assert host_env == str(host_linker)
    assert cross_env == str(cross_linker)
