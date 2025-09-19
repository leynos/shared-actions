from __future__ import annotations

import os
import subprocess
import sys
import typing as typ

import pytest

CMD_MOX_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="cmd-mox does not support Windows"
)

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from .conftest import HarnessFactory


def _register_rustup_toolchain_stub(
    cmd_mox, default_toolchain: str
) -> str:  # pragma: no cover - helper
    stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cmd_mox.stub("rustup").with_args("toolchain", "list").returns(stdout=stdout)
    return str(cmd_mox.environment.shim_dir / "rustup")


def _register_cross_version_stub(cmd_mox, stdout: str = "cross 0.2.5\n") -> str:
    cmd_mox.stub("cross").with_args("--version").returns(stdout=stdout)
    return str(cmd_mox.environment.shim_dir / "cross")


def _register_docker_info_stub(cmd_mox, *, exit_code: int = 0) -> str:
    cmd_mox.stub("docker").with_args("info").returns(exit_code=exit_code)
    return str(cmd_mox.environment.shim_dir / "docker")


@CMD_MOX_UNSUPPORTED
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

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, default_toolchain)
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emits an error when the toolchain lacks the requested target."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, default_toolchain)

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
) -> None:
    """Falls back to cargo when cross exits with a container error."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            raise subprocess.CalledProcessError(125, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, default_toolchain)
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
