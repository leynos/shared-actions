"""Tests for runtime detection helpers."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import typing as typ
from types import ModuleType, SimpleNamespace

import pytest

if typ.TYPE_CHECKING:
    from .conftest import HarnessFactory, ModuleHarness


def _patch_run_validated_timeout(
    runtime_module: ModuleType,
    harness: ModuleHarness,
    *,
    predicate: typ.Callable[[list[str]], bool] | None = None,
    success_factory: typ.Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
) -> None:
    """Patch ``run_validated`` to raise ``TimeoutExpired`` when *predicate* matches."""

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = (allowed_names, kwargs)
        cmd = [executable, *args]
        should_timeout = predicate(args) if predicate is not None else True
        if should_timeout:
            raise subprocess.TimeoutExpired(cmd, runtime_module.PROBE_TIMEOUT)
        if success_factory is not None:
            return success_factory(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)


def _reload_runtime_module(runtime_module: ModuleType, module_name: str) -> ModuleType:
    """Reload the runtime module under a new name for environment-specific tests."""
    module_path = getattr(runtime_module, "__file__", None)
    if module_path is None:
        pytest.fail("runtime module does not expose a __file__ path")
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    if module_spec is None or module_spec.loader is None:
        pytest.fail("failed to load runtime module specification")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


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
    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    harness.monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)
    _patch_run_validated_timeout(runtime_module, harness)

    assert runtime_module.runtime_available("docker") is False
    assert any(err for _, err in messages), "expected stderr warning to be emitted"
    assert any(
        "docker info probe exceeded" in msg and str(runtime_module.PROBE_TIMEOUT) in msg
        for msg, err in messages
        if err
    ), "docker info probe timeout warning missing"


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
    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    harness.monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)
    _patch_run_validated_timeout(
        runtime_module,
        harness,
        predicate=lambda args: "--format" in args,
    )

    assert runtime_module.runtime_available("podman") is False
    assert any(err for _, err in messages), "expected stderr warning to be emitted"
    assert any(
        "podman security probe exceeded" in msg
        and str(runtime_module.PROBE_TIMEOUT) in msg
        for msg, err in messages
        if err
    ), "podman security timeout warning missing"


def test_detect_host_target_returns_default_when_rustc_missing(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Falls back to the default triple when rustc is unavailable."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: None)
    assert runtime_module.detect_host_target() == runtime_module.DEFAULT_HOST_TARGET


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
    _patch_run_validated_timeout(runtime_module, harness)

    assert (
        runtime_module.detect_host_target(default="fallback-triple")
        == "fallback-triple"
    )


def test_platform_default_host_target_windows(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Windows fallbacks prefer the MSVC triple for common architectures."""
    harness = module_harness(runtime_module)
    harness.patch_attr("platform", SimpleNamespace(machine=lambda: "AMD64"))
    harness.monkeypatch.setattr(runtime_module.sys, "platform", "win32")

    assert runtime_module._platform_default_host_target() == "x86_64-pc-windows-msvc"


def test_platform_default_host_target_darwin_arm(
    runtime_module: ModuleType, module_harness: HarnessFactory
) -> None:
    """Ensure macOS ARM platforms fall back to the aarch64 Apple triple."""
    harness = module_harness(runtime_module)
    harness.patch_attr("platform", SimpleNamespace(machine=lambda: "arm64"))
    harness.monkeypatch.setattr(runtime_module.sys, "platform", "darwin")

    assert runtime_module._platform_default_host_target() == "aarch64-apple-darwin"


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


def test_probe_timeout_env_override(
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Respect RUNTIME_PROBE_TIMEOUT when importing the module."""
    monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", "2")
    module = _reload_runtime_module(runtime_module, "rbr_runtime_reloaded")
    harness = module_harness(module)

    harness.patch_shutil_which(lambda name: "/usr/bin/rustc")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)

    captured: dict[str, object] = {}

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            [executable, *args], 0, stdout="host: x86_64-unknown-linux-gnu\n"
        )

    harness.monkeypatch.setattr(module, "run_validated", fake_run)
    module.detect_host_target()
    assert captured.get("timeout") == 2


def test_probe_timeout_invalid_value_warns_and_defaults(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid timeout values emit a warning and fall back to the default."""
    monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", "not-a-number")
    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)
    module = _reload_runtime_module(runtime_module, "rbr_runtime_invalid_timeout")

    assert module.PROBE_TIMEOUT == module._DEFAULT_PROBE_TIMEOUT
    assert any(err for _, err in messages), (
        "expected stderr warning for invalid timeout"
    )
    assert any(
        "Invalid RUNTIME_PROBE_TIMEOUT value" in msg for msg, err in messages if err
    )


def test_probe_timeout_non_positive_warns_and_defaults(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero or negative timeout values are rejected with a warning."""
    monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", "0")
    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)
    module = _reload_runtime_module(runtime_module, "rbr_runtime_non_positive_timeout")

    assert module.PROBE_TIMEOUT == module._DEFAULT_PROBE_TIMEOUT
    assert any(err for _, err in messages), (
        "expected stderr warning for non-positive timeout"
    )
    assert any("must be positive" in msg for msg, err in messages if err)


def test_probe_timeout_caps_maximum(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Values above the maximum are capped and reported via warning."""
    monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", "999")
    messages: list[tuple[str, bool]] = []

    def fake_echo(message: str, *, err: bool = False) -> None:
        messages.append((message, err))

    monkeypatch.setattr(runtime_module.typer, "echo", fake_echo)
    module = _reload_runtime_module(runtime_module, "rbr_runtime_capped_timeout")

    assert module.PROBE_TIMEOUT == module._MAX_PROBE_TIMEOUT
    assert any(err for _, err in messages), "expected stderr warning for capped timeout"
    assert any("capping to" in msg for msg, err in messages if err)
