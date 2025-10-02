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


def _assert_no_timeout_trace(output: str) -> None:
    """Ensure TimeoutExpired tracebacks do not leak into CLI output."""
    assert "TimeoutExpired" not in output, output


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
@pytest.mark.parametrize(
    ("cross_available", "container_available", "expected_phrases"),
    [
        (False, False, ("cross is not installed", "no container runtime detected")),
        (False, True, ("cross is not installed",)),
        (True, False, ("no container runtime detected",)),
    ],
)
def test_container_required_target_reports_missing_prerequisites(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    capsys: pytest.CaptureFixture[str],
    *,
    cross_available: bool,
    container_available: bool,
    expected_phrases: tuple[str, ...],
) -> None:
    """Errors when prerequisites for containerized builds are missing."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    cross_path: str | None = None
    if cross_available:
        cross_path = _register_cross_version_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {"rustup": rustup_path}
        if cross_path is not None:
            mapping["cross"] = cross_path
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr(
        "ensure_cross",
        lambda *_: (cross_path, "0.2.5") if cross_available else (None, None),
    )
    app_env.patch_attr(
        "runtime_available",
        lambda runtime: container_available if runtime == "docker" else False,
    )

    cmd_mox.replay()
    with pytest.raises(main_module.typer.Exit):
        main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    err = capsys.readouterr().err
    assert "requires cross" in err
    assert ("cross" in err) or ("container runtime" in err)
    assert "missing:" in err
    for phrase in expected_phrases:
        assert phrase in err
    for phrase in {"cross is not installed", "no container runtime detected"} - set(
        expected_phrases
    ):
        assert phrase not in err


@CMD_MOX_UNSUPPORTED
def test_builds_freebsd_target_with_cross_and_container(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Succeeds when cross and a container runtime are available."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    docker_path = _register_docker_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
            "docker": docker_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda runtime: runtime == "docker")

    commands: list[list[str]] = []

    def record_run(cmd: list[str]) -> None:
        commands.append(cmd)

    app_env.patch_run_cmd(record_run)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    assert commands, "expected commands to be executed"
    assert commands[-1][0] == "cross"
    captured = capsys.readouterr()
    _assert_no_timeout_trace(captured.err)
    assert captured.err == ""


@CMD_MOX_UNSUPPORTED
def test_cross_no_docker_disallowed_for_container_required_target(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rejects CROSS_NO_DOCKER when a container runtime is required."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda _: False)
    app_env.patch_platform("win32")
    app_env.monkeypatch.setenv("CROSS_NO_DOCKER", "1")

    cmd_mox.replay()
    with pytest.raises(main_module.typer.Exit):
        main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    err = capsys.readouterr().err
    assert "requires cross" in err
    assert "CROSS_NO_DOCKER=1 is unsupported" in err


@CMD_MOX_UNSUPPORTED
def test_errors_when_cross_container_start_fails(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fails with an error when cross cannot launch the container runtime."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    docker_path = _register_docker_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
            "docker": docker_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda runtime: runtime == "docker")

    def run_cmd_side_effect(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            raise subprocess.CalledProcessError(125, cmd)

    app_env.patch_run_cmd(run_cmd_side_effect)

    cmd_mox.replay()
    with pytest.raises(main_module.typer.Exit) as exc_info:
        main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    assert exc_info.value.exit_code == 125
    err = capsys.readouterr().err
    assert "failed to start a container runtime" in err
    assert not any(call[0] == "cargo" for call in app_env.calls)


@CMD_MOX_UNSUPPORTED
def test_sets_cross_container_engine_when_docker_available(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Automatically export CROSS_CONTAINER_ENGINE when Docker is detected."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    docker_path = _register_docker_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
            "docker": docker_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda runtime: runtime == "docker")
    app_env.monkeypatch.delenv("CROSS_CONTAINER_ENGINE", raising=False)

    engines: list[str | None] = []

    def record_engine(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            engines.append(os.environ.get("CROSS_CONTAINER_ENGINE"))

    app_env.patch_run_cmd(record_engine)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    assert engines == ["docker"]
    assert "CROSS_CONTAINER_ENGINE" not in os.environ


@CMD_MOX_UNSUPPORTED
def test_sets_cross_container_engine_when_only_podman_available(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Prefers Podman when Docker is unavailable."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    podman_path = _register_podman_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
            "podman": podman_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda runtime: runtime == "podman")
    app_env.monkeypatch.delenv("CROSS_CONTAINER_ENGINE", raising=False)

    engines: list[str | None] = []

    def record_engine(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            engines.append(os.environ.get("CROSS_CONTAINER_ENGINE"))

    app_env.patch_run_cmd(record_engine)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    assert engines == ["podman"]
    assert "CROSS_CONTAINER_ENGINE" not in os.environ


@CMD_MOX_UNSUPPORTED
def test_preserves_existing_cross_container_engine(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
) -> None:
    """Does not override a pre-existing CROSS_CONTAINER_ENGINE value."""
    cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    default_toolchain = main_module.DEFAULT_TOOLCHAIN
    rustup_stdout = f"{default_toolchain}-x86_64-unknown-linux-gnu\n"
    cross_path = _register_cross_version_stub(cmd_mox)
    rustup_path = _register_rustup_toolchain_stub(cmd_mox, rustup_stdout)
    docker_path = _register_docker_info_stub(cmd_mox)

    def fake_which(name: str) -> str | None:
        mapping = {
            "rustup": rustup_path,
            "cross": cross_path,
            "docker": docker_path,
        }
        return mapping.get(name)

    cross_env.patch_shutil_which(fake_which)
    app_env.patch_shutil_which(fake_which)
    app_env.patch_attr("ensure_cross", lambda *_: (cross_path, "0.2.5"))
    app_env.patch_attr("runtime_available", lambda runtime: runtime == "docker")
    app_env.monkeypatch.setenv("CROSS_CONTAINER_ENGINE", "custom")

    engines: list[str | None] = []

    def record_engine(cmd: list[str]) -> None:
        if cmd and cmd[0] == "cross":
            engines.append(os.environ.get("CROSS_CONTAINER_ENGINE"))

    app_env.patch_run_cmd(record_engine)

    cmd_mox.replay()
    main_module.main("x86_64-unknown-freebsd", default_toolchain)
    cmd_mox.verify()

    assert engines == ["custom"]
    assert os.environ.get("CROSS_CONTAINER_ENGINE") == "custom"


@CMD_MOX_UNSUPPORTED
@pytest.mark.parametrize(
    ("target", "host_target", "expected"),
    [
        ("x86_64-unknown-freebsd", "x86_64-unknown-linux-gnu", True),
        ("x86_64-unknown-openbsd", "x86_64-unknown-linux-gnu", True),
        ("x86_64-unknown-netbsd", "x86_64-unknown-linux-gnu", True),
        ("x86_64-unknown-openbsd", "x86_64-unknown-openbsd", False),
        ("x86_64-unknown-netbsd", "x86_64-unknown-netbsd", False),
    ],
)
def test_decide_cross_usage_marks_bsd_targets_for_container(
    main_module: ModuleType,
    cross_module: ModuleType,
    module_harness: HarnessFactory,
    cmd_mox: CmdMox,
    target: str,
    host_target: str,
    *,
    expected: bool,
) -> None:
    """BSD targets require cross with containers unless host matches suffix."""
    _cross_env = module_harness(cross_module)
    app_env = module_harness(main_module)

    app_env.patch_attr("ensure_cross", lambda *_: ("/fake/cross", "0.2.5"))
    app_env.patch_attr("_probe_runtime", lambda _name: False)

    cmd_mox.replay()

    decision = main_module._decide_cross_usage(
        toolchain_name="stable-x86_64-unknown-linux-gnu",
        installed_names=["stable-x86_64-unknown-linux-gnu"],
        rustup_exec="rustup",
        target=target,
        host_target=host_target,
    )

    cmd_mox.verify()
    assert decision.requires_cross_container is expected


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
    capsys: pytest.CaptureFixture[str],
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
    captured = capsys.readouterr()
    assert (
        "cross (0.2.5) requires a container runtime; "
        "using cargo (docker=False, podman=False)" in captured.out
    )
    _assert_no_timeout_trace(captured.err)


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
    ("host_platform", "target", "should_probe"),
    [
        ("win32", "x86_64-pc-windows-msvc", False),
        ("win32", "aarch64-pc-windows-gnu", False),
        ("win32", "x86_64-uwp-windows-msvc", False),
        ("win32", "x86_64-pc-windows-gnullvm", False),
        ("win32", "x86_64-unknown-linux-gnu", True),
        ("linux", "x86_64-pc-windows-msvc", True),
        ("linux", "x86_64-unknown-linux-gnu", False),
    ],
)
def test_should_probe_container_handles_windows_targets(
    main_module: ModuleType,
    host_platform: str,
    target: str,
    should_probe: bool,  # noqa: FBT001
) -> None:
    """Helper correctly decides when to probe container runtimes."""
    assert main_module.should_probe_container(host_platform, target) is should_probe


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


def test_probe_runtime_warns_on_timeout_without_duration(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Timeout warnings omit duration when the exception lacks a timeout."""
    harness = module_harness(main_module)

    def raise_timeout(name: str) -> bool:
        _ = name
        raise subprocess.TimeoutExpired(cmd="docker info", timeout=None)

    harness.patch_attr("runtime_available", raise_timeout)

    assert main_module._probe_runtime("docker") is False

    err = capsys.readouterr().err
    expected = (
        "::warning::docker runtime probe timed out; treating runtime as unavailable"
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
    """Treat runtime probe timeouts as unavailable while still completing the build."""
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

    commands: list[list[str]] = []

    def record_run_cmd(cmd: list[str]) -> None:
        commands.append(cmd)
        if cmd[:3] == ["/usr/bin/rustup", "target", "add"]:
            return
        if cmd and cmd[0] == "cargo":
            return
        pytest.fail(f"unexpected run_cmd call: {cmd}")

    harness.patch_run_cmd(record_run_cmd)
    harness.patch_attr("configure_windows_linkers", lambda *_: None)

    def timeout_runtime(_name: str, *, cwd: object | None = None) -> bool:
        _ = cwd
        raise subprocess.TimeoutExpired(cmd="podman info", timeout=10)

    harness.patch_attr("runtime_available", timeout_runtime)
    harness.patch_attr("ensure_cross", lambda *_: (None, None))

    main_module.main("thumbv7em-none-eabihf", default_toolchain)

    out, err = capsys.readouterr()
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
    assert "cross missing; using cargo" in out
    _assert_no_timeout_trace(err)

    assert len(commands) >= 2
    assert commands[0][:3] == ["/usr/bin/rustup", "target", "add"]
    assert commands[1][0] == "cargo"
    assert commands[1][1].startswith("+")
    assert commands[1][-1] == "thumbv7em-none-eabihf"


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
