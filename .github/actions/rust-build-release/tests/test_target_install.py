"""Tests target installation fallback behavior."""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from .conftest import HarnessFactory


def test_skips_target_install_when_cross_available(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
) -> None:
    """Continues when target addition fails but cross is available."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd[:3] == ["rustup", "target", "add"]:
            raise subprocess.CalledProcessError(1, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    def fake_which(name: str) -> str | None:
        mapping = {
            "cross": "/usr/bin/cross",
            "docker": "/usr/bin/docker",
            "rustup": "/usr/bin/rustup",
        }
        return mapping.get(name)

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
        if executable == "/usr/bin/docker":
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        if len(cmd) > 1 and cmd[1] == "toolchain":
            stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        return subprocess.CompletedProcess(cmd, 0, stdout="cross 0.2.5\n")

    cross_env.patch_subprocess_run(fake_run)
    app_env.patch_subprocess_run(fake_run)

    main_module.main("aarch64-pc-windows-gnu", default_toolchain)
    build_cmd = app_env.calls[-1]
    assert build_cmd[0] == "cross"
    assert build_cmd[1] == f"+{default_toolchain}"


def test_errors_when_target_unsupported_without_cross(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emits an error when the toolchain lacks the requested target."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def fake_which(name: str) -> str | None:
        return "/usr/bin/rustup" if name == "rustup" else None

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
        if len(cmd) > 1 and cmd[1] == "toolchain":
            stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    cross_env.patch_subprocess_run(fake_run)
    app_env.patch_subprocess_run(fake_run)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd[:3] == ["rustup", "target", "add"]:
            raise subprocess.CalledProcessError(1, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)
    app_env.patch_attr("ensure_cross", lambda *_: (None, None))
    app_env.patch_attr("runtime_available", lambda name: False)

    with pytest.raises(main_module.typer.Exit):
        main_module.main("thumbv7em-none-eabihf", default_toolchain)

    err = capsys.readouterr().err
    assert "does not support target 'thumbv7em-none-eabihf'" in err


def test_falls_back_to_cargo_when_cross_container_fails(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
) -> None:
    """Falls back to cargo when cross exits with a container error."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            raise subprocess.CalledProcessError(125, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": "/usr/bin/rustup",
        }
        return mapping.get(name)

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
        if len(cmd) > 1 and cmd[1] == "toolchain":
            stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    cross_env.patch_subprocess_run(fake_run)
    app_env.patch_subprocess_run(fake_run)
    app_env.patch_attr("ensure_cross", lambda required: ("/usr/bin/cross", required))
    app_env.patch_attr("runtime_available", lambda name: True)

    main_module.main("x86_64-unknown-linux-gnu", default_toolchain)
    build_cmd = app_env.calls[-1]
    assert build_cmd[0] == "cargo"
    assert build_cmd[1] == f"+{default_toolchain}-x86_64-unknown-linux-gnu"


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

    assert runtime_calls == ["docker", "podman"]


@pytest.mark.parametrize(
    ("host_platform", "target", "expected"),
    [
        ("win32", "x86_64-pc-windows-msvc", False),
        ("win32", "aarch64-pc-windows-gnu", False),
        ("win32", "x86_64-uwp-windows-msvc", False),
        ("win32", "x86_64-pc-windows-gnullvm", False),
        ("win32", "x86_64-unknown-linux-gnu", True),
        ("linux", "x86_64-pc-windows-msvc", True),
    ],
)
def test_should_probe_container_handles_windows_targets(
    main_module: ModuleType,
    host_platform: str,
    target: str,
    expected: bool,
) -> None:
    """Helper correctly decides when to probe container runtimes."""

    assert main_module.should_probe_container(host_platform, target) is expected


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
