"""Tests for runtime detection helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
import typing as typ
from types import ModuleType, SimpleNamespace

import pytest
from plumbum.commands.processes import ProcessTimedOut

if typ.TYPE_CHECKING:
    from .conftest import HarnessFactory, ModuleHarness


RunOutput = tuple[int, str, str]


def _run_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> RunOutput:
    """Return a tuple matching the run_cmd ``method='run'`` contract."""
    return (returncode, stdout, stderr)


def _patch_run_validated_timeout(
    runtime_module: ModuleType,
    harness: ModuleHarness,
    *,
    predicate: typ.Callable[[list[str]], bool] | None = None,
    success_factory: typ.Callable[[list[str]], RunOutput] | None = None,
) -> None:
    """Patch ``run_validated`` to raise ``ProcessTimedOut`` when *predicate* matches."""

    def fake_run(
        executable: str,
        args: list[str],
        *,
        allowed_names: tuple[str, ...],
        **kwargs: object,
    ) -> RunOutput:
        _ = (allowed_names, kwargs)
        cmd = [executable, *args]
        should_timeout = predicate(args) if predicate is not None else True
        if should_timeout:
            message = "probe timed out"
            raise ProcessTimedOut(message, cmd)
        if success_factory is not None:
            return success_factory(cmd)
        return _run_result()

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
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
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
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
) -> None:
    """Treats runtimes that hang during discovery as unavailable."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/docker")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)
    messages = echo_recorder(runtime_module)
    _patch_run_validated_timeout(runtime_module, harness)

    assert runtime_module.runtime_available("docker") is False
    assert any(err for _, err in messages), "expected stderr warning to be emitted"
    assert any(
        "docker info probe exceeded" in msg and str(runtime_module.PROBE_TIMEOUT) in msg
        for msg, err in messages
        if err
    ), "docker info probe timeout warning missing"


def test_runtime_available_oserror_does_not_warn(
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
) -> None:
    """OSError during runtime detection should not emit warnings."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/docker")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)
    messages = echo_recorder(runtime_module)

    def fake_run(*_: object, **__: object) -> RunOutput:
        message = "simulated OSError"
        raise OSError(message)

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert runtime_module.runtime_available("docker") is False
    assert not any(err for _, err in messages), "unexpected warning for OSError"


def test_podman_without_cap_sys_admin_is_unavailable(
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
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
    ) -> RunOutput:
        _ = allowed_names
        if "--format" in args:
            data = json.dumps({"capabilities": "CAP_NET_ADMIN"})
            return _run_result(stdout=data)
        return _run_result()

    messages = echo_recorder(runtime_module)
    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

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
    ) -> RunOutput:
        _ = allowed_names
        if "--format" in args:
            data = json.dumps({"capabilities": ["CAP_SYS_ADMIN"]})
            return _run_result(stdout=data)
        return _run_result()

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)
    assert runtime_module.runtime_available("podman") is True


def test_podman_security_timeout_treated_as_unavailable(
    runtime_module: ModuleType,
    module_harness: HarnessFactory,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
) -> None:
    """If podman security inspection times out the runtime is skipped."""
    harness = module_harness(runtime_module)
    harness.patch_shutil_which(lambda name: "/usr/bin/podman")
    harness.patch_attr("ensure_allowed_executable", lambda path, allowed: path)
    messages = echo_recorder(runtime_module)
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
    ) -> RunOutput:
        _ = (allowed_names, capture_output, check, text)
        return _run_result(stdout="host: custom-triple\n")

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
    ) -> RunOutput:
        _ = (executable, args)
        call_kwargs.update(kwargs)
        call_kwargs["allowed_names"] = allowed_names
        return _run_result(stdout="host: bounded\n")

    harness.monkeypatch.setattr(runtime_module, "run_validated", fake_run)

    assert runtime_module.detect_host_target() == "bounded"
    assert call_kwargs.get("timeout") == runtime_module.PROBE_TIMEOUT
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
    ) -> RunOutput:
        captured.update(kwargs)
        return _run_result(stdout="host: x86_64-unknown-linux-gnu\n")

    harness.monkeypatch.setattr(module, "run_validated", fake_run)
    module.detect_host_target()
    assert captured.get("timeout") == 2


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("AMD64", "x86_64"),
        ("x64", "x86_64"),
        ("i386", "i686"),
        ("I586", "i686"),
        ("ARM64", "aarch64"),
        ("armv8l", "aarch64"),
        ("ARMV7L", "armv7"),
        ("armv6l", "armv6"),
        ("PPC64LE", "ppc64le"),
        ("PowerPC64", "ppc64"),
        ("sparc64", "sparc64"),
    ],
)
def test_normalize_arch_unit_mappings(
    runtime_module: ModuleType, machine: str, expected: str
) -> None:
    """Unit test: known architecture identifiers normalize correctly."""
    assert runtime_module._normalize_arch(machine) == expected


def test_normalize_arch_behavioral_fallbacks(runtime_module: ModuleType) -> None:
    """Behavioural test: unknown and missing machine names are handled."""
    assert runtime_module._normalize_arch("") == "x86_64"
    # Unknown identifiers are normalized to lowercase for stability.
    assert runtime_module._normalize_arch("Loongson") == "loongson"


@pytest.mark.parametrize(
    ("system_name", "machine", "sys_platform", "expected"),
    [
        ("Windows", "AMD64", "win32", "x86_64-pc-windows-msvc"),
        ("CYGWIN_NT-10.0", "x86_64", "cygwin", "x86_64-pc-windows-gnu"),
        ("MSYS_NT-10.0", "x86_64", "msys", "x86_64-pc-windows-gnu"),
        ("Darwin", "arm64", "darwin", "aarch64-apple-darwin"),
        ("Linux", "ppc64le", "linux", "ppc64le-unknown-linux-gnu"),
        ("Linux-gnu", "armv7l", "linux-gnu", "armv7-unknown-linux-gnu"),
        ("FreeBSD", "sparc64", "freebsd13", "sparc64-unknown-freebsd"),
    ],
)
def test_default_host_target_for_current_platform_unit(
    runtime_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    machine: str,
    sys_platform: str,
    expected: str,
) -> None:
    """Unit test: platform/architecture combinations map to expected triples."""
    monkeypatch.setattr(runtime_module.platform, "system", lambda: system_name)
    monkeypatch.setattr(runtime_module.platform, "machine", lambda: machine)
    monkeypatch.setattr(runtime_module.sys, "platform", sys_platform)

    assert runtime_module._default_host_target_for_current_platform() == expected


def test_default_host_target_for_current_platform_behavioral_fallback(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavioural test: fallbacks cover missing identifiers."""
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "")
    monkeypatch.setattr(runtime_module.platform, "machine", lambda: "")
    monkeypatch.setattr(runtime_module.sys, "platform", "customos")

    assert (
        runtime_module._default_host_target_for_current_platform()
        == "x86_64-unknown-customos"
    )


@pytest.mark.parametrize(
    ("env_value", "expected_kind", "message_fragment"),
    [
        pytest.param(
            "not-a-number",
            "default",
            "Invalid RUNTIME_PROBE_TIMEOUT value",
            id="invalid",
        ),
        pytest.param("0", "default", "0s raised to", id="zero"),
        pytest.param("-5", "default", "-5s raised to", id="negative"),
        pytest.param("999", "max", "999s capped to", id="capped"),
    ],
)
def test_probe_timeout_sanitization_warnings(
    runtime_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
    request: pytest.FixtureRequest,
    env_value: str,
    expected_kind: str,
    message_fragment: str,
) -> None:
    """Probe timeout overrides produce warnings when sanitized."""
    messages = echo_recorder(runtime_module)
    monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", env_value)
    module = _reload_runtime_module(
        runtime_module,
        f"rbr_runtime_timeout_{request.node.callspec.id}",
    )
    if expected_kind == "default":
        expected = module._DEFAULT_PROBE_TIMEOUT
    elif expected_kind == "max":
        expected = module._MAX_PROBE_TIMEOUT
    else:
        expected = int(env_value)
    assert expected == module.PROBE_TIMEOUT
    assert any(err for _, err in messages), "expected stderr warning for timeout"
    assert any(
        message_fragment in msg and str(expected) in msg for msg, err in messages if err
    )


@pytest.mark.parametrize(
    ("env_value", "expected_kind", "message_fragment"),
    [
        pytest.param(None, "default", None, id="unset"),
        pytest.param("5", "value", None, id="custom-value"),
        pytest.param(
            "not-a-number",
            "default",
            "Invalid RUNTIME_PROBE_TIMEOUT value",
            id="invalid",
        ),
        pytest.param("0", "default", "0s raised to", id="zero"),
        pytest.param("-5", "default", "-5s raised to", id="negative"),
        pytest.param("999", "max", "999s capped to", id="capped"),
    ],
)
def test_get_probe_timeout_sanitizes_values(
    runtime_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    echo_recorder: typ.Callable[[ModuleType], list[tuple[str, bool]]],
    env_value: str | None,
    expected_kind: str,
    message_fragment: str | None,
) -> None:
    """Unit tests for probe timeout sanitization helper."""
    messages = echo_recorder(runtime_module)
    if env_value is None:
        monkeypatch.delenv("RUNTIME_PROBE_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("RUNTIME_PROBE_TIMEOUT", env_value)
    result = runtime_module._get_probe_timeout()
    if expected_kind == "default":
        expected = runtime_module._DEFAULT_PROBE_TIMEOUT
    elif expected_kind == "max":
        expected = runtime_module._MAX_PROBE_TIMEOUT
    else:
        expected = int(env_value or runtime_module._DEFAULT_PROBE_TIMEOUT)
    assert result == expected
    if message_fragment is None:
        assert not any(err for _, err in messages), "unexpected stderr warning"
    else:
        assert any(err for _, err in messages), "expected stderr warning"
        assert any(
            message_fragment in msg and str(expected) in msg
            for msg, err in messages
            if err
        )
