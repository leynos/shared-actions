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


@pytest.mark.parametrize(
    "config",
    [
        BuildTestConfig("cross", "aarch64-unknown-linux-gnu", "1.89.0"),
        BuildTestConfig("cargo", "x86_64-unknown-linux-gnu", "stable"),
    ],
    ids=["cross", "cargo"],
)
def test_main_passes_manifest_to_builder(
    main_module: ModuleType,
    patch_common_main_deps: ModuleHarness,
    setup_manifest: Path,
    config: BuildTestConfig,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> None:
    """Ensure both build modes receive the manifest path."""
    harness = patch_common_main_deps
    captured: dict[str, object] = {}

    if config.build_mode == "cross":
        harness.patch_attr("_resolve_target_argument", lambda _value: config.target)
        harness.patch_attr(
            "_resolve_toolchain", lambda *_: (config.toolchain, [config.toolchain])
        )
        decision = cross_decision_factory(main_module, use_cross=True)

        def fake_cross(
            decision_arg: object, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["decision"] = decision_arg
            captured["target"] = target_arg
            captured["manifest"] = manifest_arg
            captured["features"] = features_arg
            return dummy_command_factory("cross-build")

        harness.patch_attr("_build_cross_command", fake_cross)
        harness.patch_attr(
            "_build_cargo_command", _unexpected("unexpected cargo build")
        )
    else:
        harness.patch_attr("_resolve_target_argument", lambda _value: _value)
        decision = cross_decision_factory(main_module, use_cross=False)

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["target"] = target_arg
            captured["manifest"] = manifest_arg
            captured["features"] = features_arg
            return dummy_command_factory("cargo-build")

        harness.patch_attr("_build_cargo_command", fake_cargo)
        harness.patch_attr(
            "_build_cross_command", _unexpected("unexpected cross build")
        )

    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

    main_module.main(config.target, toolchain=config.toolchain)

    assert captured["manifest"] == Path("Cargo.toml")
    assert captured["features"] == ""


def test_main_errors_when_manifest_missing(
    main_module: ModuleType,
    patch_common_main_deps: ModuleHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
) -> None:
    """main() should fail fast without a manifest present."""
    harness = patch_common_main_deps
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)

    target = "x86_64-unknown-linux-gnu"
    harness.patch_attr("_resolve_target_argument", lambda value: target)
    decision = cross_decision_factory(main_module, use_cross=False)
    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)
    harness.patch_attr("_build_cargo_command", _unexpected("unexpected build"))

    with pytest.raises(main_module.typer.Exit):
        main_module.main(target, toolchain="stable")

    assert harness.calls == []


@pytest.mark.parametrize(
    ("builder", "target"),
    [
        ("cross", "x86_64-unknown-linux-gnu"),
        ("cargo", "x86_64-unknown-linux-gnu"),
    ],
)
def test_build_commands_include_manifest_path(
    main_module: ModuleType,
    tmp_path: Path,
    builder: str,
    target: str,
    cross_decision_factory: CrossDecisionFactory,
) -> None:
    """Both builders must embed the manifest path flag."""
    manifest = (tmp_path / "Cargo.toml").resolve()
    if builder == "cross":
        decision = cross_decision_factory(main_module, use_cross=True)
        cmd = main_module._build_cross_command(decision, target, manifest, "")
    else:
        cmd = main_module._build_cargo_command("+stable", target, manifest, "")

    assert_manifest_in_command(cmd, manifest)


def test_handle_cross_container_error_passes_manifest_to_fallback(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> None:
    """Fallback cargo builds must see the manifest path as well."""
    harness = module_harness(main_module)
    manifest = (tmp_path / "Cargo.toml").resolve()
    captured: dict[str, object] = {}

    def fake_cargo(
        _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
    ) -> object:
        captured["target"] = target_arg
        captured["manifest"] = manifest_arg
        captured["features"] = features_arg
        return dummy_command_factory("fallback")

    harness.patch_attr("_build_cargo_command", fake_cargo)

    decision = cross_decision_factory(main_module, use_cross=True)
    exc = ProcessExecutionError(["cross"], 125, "", "")

    main_module._handle_cross_container_error(exc, decision, "aarch64", manifest, "")

    assert captured["manifest"] == manifest
    assert harness.calls
    assert harness.calls[0] == ["fallback"]
