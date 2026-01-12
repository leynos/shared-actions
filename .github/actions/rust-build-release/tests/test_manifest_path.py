"""Tests covering manifest resolution and build wiring."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path
from types import ModuleType

import pytest
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from .conftest import (
        CrossDecision,
        CrossDecisionFactory,
        DummyCommandFactory,
        HarnessFactory,
        ModuleHarness,
    )


EchoRecorder = cabc.Callable[[ModuleType], list[tuple[str, bool]]]


def _unexpected(message: str) -> cabc.Callable[..., None]:
    def _raiser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(message)

    return _raiser


def assert_manifest_in_command(cmd: object, expected: Path) -> None:
    """Verify a formulated command includes the expected manifest path."""
    parts = list(cmd.formulate())
    assert "--manifest-path" in parts
    idx = parts.index("--manifest-path")
    assert parts[idx + 1] == str(expected)


def test_resolve_manifest_path_defaults_to_cwd(
    main_module: ModuleType, setup_manifest: Path
) -> None:
    """Should locate Cargo.toml in the current working directory."""
    resolved = main_module._resolve_manifest_path()

    assert resolved == setup_manifest.resolve()


def test_resolve_manifest_path_uses_env_override(
    main_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Environment overrides should take precedence over CWD manifests."""
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RBR_MANIFEST_PATH", str(manifest))

    resolved = main_module._resolve_manifest_path()

    assert resolved == manifest.resolve()


def test_resolve_manifest_path_errors_when_missing(
    main_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: EchoRecorder,
) -> None:
    """Report an error when no manifest exists."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)
    messages = echo_recorder(main_module)

    with pytest.raises(main_module.typer.Exit):
        main_module._resolve_manifest_path()

    assert any("Cargo manifest not found" in msg for msg, _ in messages)


def test_manifest_argument_prefers_relative_paths(
    main_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative paths are preferred when the manifest resides under cwd."""
    manifest = (tmp_path / "Cargo.toml").resolve()
    monkeypatch.chdir(tmp_path)

    argument = main_module._manifest_argument(manifest)

    assert argument == Path("Cargo.toml")


def test_manifest_argument_returns_absolute_outside_cwd(
    main_module: ModuleType, tmp_path: Path
) -> None:
    """Absolute paths are preserved for manifests outside cwd."""
    manifest = (tmp_path / "Cargo.toml").resolve()

    argument = main_module._manifest_argument(manifest)

    assert argument == manifest


@dc.dataclass
class BuildTestConfig:
    """Configuration for build mode test scenarios."""

    build_mode: str
    target: str
    toolchain: str


@dc.dataclass(frozen=True)
class BuildMainContext:
    """Bundled dependencies for main() manifest tests."""

    main_module: ModuleType
    harness: ModuleHarness
    cross_decision_factory: CrossDecisionFactory
    dummy_command_factory: DummyCommandFactory


@dc.dataclass(frozen=True)
class BuildCommandContext:
    """Bundled dependencies for build command tests."""

    main_module: ModuleType
    manifest: Path
    cross_decision: CrossDecision


@dc.dataclass(frozen=True)
class CrossFallbackContext:
    """Bundled dependencies for cross fallback tests."""

    main_module: ModuleType
    harness: ModuleHarness
    manifest: Path
    decision: CrossDecision
    dummy_command_factory: DummyCommandFactory


@pytest.fixture
def build_main_context(
    main_module: ModuleType,
    patch_common_main_deps: ModuleHarness,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> BuildMainContext:
    """Build a context for main() manifest tests."""
    return BuildMainContext(
        main_module=main_module,
        harness=patch_common_main_deps,
        cross_decision_factory=cross_decision_factory,
        dummy_command_factory=dummy_command_factory,
    )


@pytest.fixture
def build_command_context(
    main_module: ModuleType,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
) -> BuildCommandContext:
    """Build a context for manifest-aware command tests."""
    return BuildCommandContext(
        main_module=main_module,
        manifest=(tmp_path / "Cargo.toml").resolve(),
        cross_decision=cross_decision_factory(main_module, use_cross=True),
    )


@pytest.fixture
def main_module_harness(
    main_module: ModuleType, module_harness: HarnessFactory
) -> ModuleHarness:
    """Return a module harness for main.py tests."""
    return module_harness(main_module)


@pytest.fixture
def cross_fallback_context(
    main_module_harness: ModuleHarness,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> CrossFallbackContext:
    """Build a context for cross container fallback tests."""
    main_module = main_module_harness.module
    return CrossFallbackContext(
        main_module=main_module,
        harness=main_module_harness,
        manifest=(tmp_path / "Cargo.toml").resolve(),
        decision=cross_decision_factory(main_module, use_cross=True),
        dummy_command_factory=dummy_command_factory,
    )


@pytest.mark.parametrize(
    "config",
    [
        BuildTestConfig("cross", "aarch64-unknown-linux-gnu", "1.89.0"),
        BuildTestConfig("cargo", "x86_64-unknown-linux-gnu", "stable"),
    ],
    ids=["cross", "cargo"],
)
@pytest.mark.usefixtures("setup_manifest")
def test_main_passes_manifest_to_builder(
    build_main_context: BuildMainContext,
    config: BuildTestConfig,
) -> None:
    """Ensure both build modes receive the manifest path."""
    context = build_main_context
    harness = context.harness
    captured: dict[str, object] = {}

    if config.build_mode == "cross":
        harness.patch_attr("_resolve_target_argument", lambda _value: config.target)
        harness.patch_attr(
            "_resolve_toolchain", lambda *_: (config.toolchain, [config.toolchain])
        )
        decision = context.cross_decision_factory(context.main_module, use_cross=True)

        def fake_cross(
            decision_arg: object, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["decision"] = decision_arg
            captured["target"] = target_arg
            captured["manifest"] = manifest_arg
            captured["features"] = features_arg
            return context.dummy_command_factory("cross-build")

        harness.patch_attr("_build_cross_command", fake_cross)
        harness.patch_attr(
            "_build_cargo_command", _unexpected("unexpected cargo build")
        )
    else:
        harness.patch_attr("_resolve_target_argument", lambda _value: _value)
        decision = context.cross_decision_factory(context.main_module, use_cross=False)

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["target"] = target_arg
            captured["manifest"] = manifest_arg
            captured["features"] = features_arg
            return context.dummy_command_factory("cargo-build")

        harness.patch_attr("_build_cargo_command", fake_cargo)
        harness.patch_attr(
            "_build_cross_command", _unexpected("unexpected cross build")
        )

    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

    context.main_module.main(config.target, toolchain=config.toolchain)

    assert captured["manifest"] == Path("Cargo.toml")
    assert captured["features"] == ""


def test_main_errors_when_manifest_missing(
    build_main_context: BuildMainContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """main() should fail fast without a manifest present."""
    context = build_main_context
    harness = context.harness
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)

    target = "x86_64-unknown-linux-gnu"
    harness.patch_attr("_resolve_target_argument", lambda _value: target)
    decision = context.cross_decision_factory(context.main_module, use_cross=False)
    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)
    harness.patch_attr("_build_cargo_command", _unexpected("unexpected build"))

    with pytest.raises(context.main_module.typer.Exit):
        context.main_module.main(target, toolchain="stable")

    assert harness.calls == []


@pytest.mark.parametrize(
    ("builder", "target"),
    [
        ("cross", "x86_64-unknown-linux-gnu"),
        ("cargo", "x86_64-unknown-linux-gnu"),
    ],
)
def test_build_commands_include_manifest_path(
    build_command_context: BuildCommandContext,
    builder: str,
    target: str,
) -> None:
    """Both builders must embed the manifest path flag."""
    context = build_command_context
    if builder == "cross":
        cmd = context.main_module._build_cross_command(
            context.cross_decision, target, context.manifest, ""
        )
    else:
        cmd = context.main_module._build_cargo_command(
            "+stable", target, context.manifest, ""
        )

    assert_manifest_in_command(cmd, context.manifest)


def test_handle_cross_container_error_passes_manifest_to_fallback(
    cross_fallback_context: CrossFallbackContext,
) -> None:
    """Fallback cargo builds must see the manifest path as well."""
    context = cross_fallback_context
    harness = context.harness
    captured: dict[str, object] = {}

    def fake_cargo(
        _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
    ) -> object:
        captured["target"] = target_arg
        captured["manifest"] = manifest_arg
        captured["features"] = features_arg
        return context.dummy_command_factory("fallback")

    harness.patch_attr("_build_cargo_command", fake_cargo)

    exc = ProcessExecutionError(["cross"], 125, "", "")

    context.main_module._handle_cross_container_error(
        exc, context.decision, "aarch64", context.manifest, ""
    )

    assert captured["manifest"] == context.manifest
    assert captured["features"] == ""
    assert harness.calls
    assert harness.calls[0] == ["fallback"]
