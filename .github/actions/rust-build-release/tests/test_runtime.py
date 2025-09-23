"""Tests for runtime detection helpers."""

from __future__ import annotations

import json
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

    from .conftest import HarnessFactory


def test_runtime_available_false_when_missing(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Returns False when the runtime binary cannot be located."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: None)
    assert runtime_module.runtime_available("docker") is False


def test_runtime_available_requires_allowed_executable(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Returns False when executable validation fails."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/docker")

    def fake_ensure(path: str, allowed: tuple[str, ...]) -> str:
        raise runtime_module.UnexpectedExecutableError(path)

    harness.patch_attr("ensure_allowed_executable", fake_ensure)
    assert runtime_module.runtime_available("docker") is False


def test_runtime_available_returns_false_on_timeout(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Treats runtimes that hang during discovery as unavailable."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/docker")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = allowed_names
        cmd = [executable, *args]
        raise subprocess.TimeoutExpired(cmd, runtime_module.PROBE_TIMEOUT)

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert runtime_module.runtime_available("docker") is False


def test_podman_without_cap_sys_admin_is_unavailable(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Podman runtimes lacking CAP_SYS_ADMIN are reported as unavailable."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/podman")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

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
        if "--format" in args:
            data = json.dumps({"capabilities": "CAP_NET_ADMIN"})
            return subprocess.CompletedProcess(cmd, 0, stdout=data)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)
    harness.monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)

    assert runtime_module.runtime_available("podman") is False
    assert any("CAP_SYS_ADMIN" in msg for msg, err in messages if err)


def test_podman_with_cap_sys_admin_is_available(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Podman runtimes with CAP_SYS_ADMIN are considered available."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/podman")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

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
        if "--format" in args:
            data = json.dumps({"capabilities": ["CAP_SYS_ADMIN"]})
            return subprocess.CompletedProcess(cmd, 0, stdout=data)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)
    assert runtime_module.runtime_available("podman") is True


def test_podman_security_timeout_treated_as_unavailable(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """If podman security inspection times out the runtime is skipped."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/podman")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

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
        _ = (allowed_names, capture_output, check, text)
        cmd = [executable, *args]
        if "--format" in args:
            raise subprocess.TimeoutExpired(cmd, runtime_module.PROBE_TIMEOUT)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert runtime_module.runtime_available("podman") is False


def test_detect_host_target_returns_default_when_rustc_missing(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Falls back to the default triple when rustc is unavailable."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: None)
    assert runtime_module.detect_host_target() == runtime_module.DEFAULT_HOST_TARGET


@pytest.mark.parametrize(
    ("platform_name", "machine", "expected"),
    (
        ("win32", "AMD64", "x86_64-pc-windows-msvc"),
        ("win32", "ARM64", "aarch64-pc-windows-msvc"),
        ("darwin", "x86_64", "x86_64-apple-darwin"),
        ("darwin", "arm64", "aarch64-apple-darwin"),
        ("linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("linux", "aarch64", "aarch64-unknown-linux-gnu"),
        ("linux", "armv7l", "armv7-unknown-linux-gnueabihf"),
        ("plan9", "mips", "x86_64-unknown-linux-gnu"),
    ),
)
def test_platform_default_host_target_matrix(
    runtime_module: ModuleType, platform_name: str, machine: str, expected: str
) -> None:
    """Derives platform-appropriate fallbacks for common triples."""

    result = runtime_module._platform_default_host_target(platform_name, machine)
    assert result == expected


def test_detect_host_target_parses_rustc_output(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Parses the host triple from rustc version output."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(
        lambda name: "/usr/bin/rustc" if name == "rustc" else None
    )
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

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
        _ = (allowed_names, capture_output, check, text)
        cmd = [executable, *args]
        return subprocess.CompletedProcess(cmd, 0, stdout="host: custom-triple\n")

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)
    assert runtime_module.detect_host_target() == "custom-triple"


def test_detect_host_target_returns_default_on_timeout(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Falls back to the default triple when rustc probing times out."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(
        lambda name: "/usr/bin/rustc" if name == "rustc" else None
    )
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = (executable, args, allowed_names)
        raise subprocess.TimeoutExpired(
            [executable, *args], runtime_module.PROBE_TIMEOUT
        )

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert (
        runtime_module.detect_host_target(default="fallback-triple")
        == "fallback-triple"
    )


def test_detect_host_target_passes_timeout_to_run_validated(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Ensures rustc probing is bounded via the timeout parameter."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(
        lambda name: "/usr/bin/rustc" if name == "rustc" else None
    )
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

    call_kwargs: dict[str, object] = {}

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = (executable, args)
        call_kwargs.update(kwargs)
        call_kwargs["allowed_names"] = allowed_names
        return subprocess.CompletedProcess(
            [executable, *args], 0, stdout="host: bounded\n"
        )

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert runtime_module.detect_host_target() == "bounded"
    assert call_kwargs.get("timeout") == runtime_module.PROBE_TIMEOUT
    assert call_kwargs.get("capture_output") is True
    assert call_kwargs.get("text") is True
    assert call_kwargs.get("check") is True
    assert call_kwargs.get("allowed_names") == ("rustc", "rustc.exe")
