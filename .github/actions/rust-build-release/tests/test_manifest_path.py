"""Tests covering manifest resolution and build wiring."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path
from types import ModuleType

import pytest
from plumbum.commands.processes import ProcessExecutionError

# ruff: noqa: D103


class Harness(typ.Protocol):
    """Protocol describing the minimal harness interface used in tests."""

    calls: list[list[str]]

    def patch_attr(self, name: str, value: object) -> None:
        """Patch an attribute on the wrapped module."""


HarnessFactory = cabc.Callable[[ModuleType], Harness]
EchoRecorder = cabc.Callable[[ModuleType], list[tuple[str, bool]]]


class _DummyCommand:
    def __init__(self, name: str = "dummy") -> None:
        self._name = name

    def formulate(self) -> list[str]:
        return [self._name]

    def __call__(self, *args: object, **kwargs: object) -> None:
        return None


def _cross_decision(
    main_module: ModuleType, *, use_cross: bool, requires_container: bool = False
) -> object:
    return main_module._CrossDecision(  # type: ignore[attr-defined]
        cross_path="/usr/bin/cross" if use_cross else None,
        cross_version="0.2.5",
        use_cross=use_cross,
        cross_toolchain_spec="+stable",
        cargo_toolchain_spec="+stable",
        use_cross_local_backend=False,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker" if use_cross else None,
        requires_cross_container=requires_container,
    )


def _unexpected(message: str) -> cabc.Callable[..., None]:
    def _raiser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(message)

    return _raiser


@pytest.fixture
def setup_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)
    return manifest


@pytest.fixture
def patch_common_main_deps(
    main_module: ModuleType, module_harness: HarnessFactory
) -> Harness:
    harness: Harness = module_harness(main_module)
    harness.patch_attr("_ensure_rustup_exec", lambda: "/usr/bin/rustup")
    harness.patch_attr("_resolve_toolchain", lambda *_: ("stable", ["stable"]))
    harness.patch_attr("_ensure_target_installed", lambda *_: True)
    harness.patch_attr("configure_windows_linkers", lambda *_, **__: None)
    harness.patch_attr("_configure_cross_container_engine", lambda *_: (None, None))
    harness.patch_attr("_restore_container_engine", lambda *_, **__: None)
    return harness


def assert_manifest_in_command(cmd: object, expected: Path) -> None:
    parts = list(cmd.formulate())
    assert "--manifest-path" in parts
    idx = parts.index("--manifest-path")
    assert parts[idx + 1] == str(expected)


def test_resolve_manifest_path_defaults_to_cwd(
    main_module: ModuleType, setup_manifest: Path
) -> None:
    resolved = main_module._resolve_manifest_path()

    assert resolved == setup_manifest.resolve()


def test_resolve_manifest_path_uses_env_override(
    main_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)
    messages = echo_recorder(main_module)

    with pytest.raises(main_module.typer.Exit):
        main_module._resolve_manifest_path()

    assert any("Cargo manifest not found" in msg for msg, _ in messages)


def test_manifest_argument_prefers_relative_paths(
    main_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = (tmp_path / "Cargo.toml").resolve()
    monkeypatch.chdir(tmp_path)

    argument = main_module._manifest_argument(manifest)

    assert argument == Path("Cargo.toml")


def test_manifest_argument_returns_absolute_outside_cwd(
    main_module: ModuleType, tmp_path: Path
) -> None:
    manifest = (tmp_path / "Cargo.toml").resolve()

    argument = main_module._manifest_argument(manifest)

    assert argument == manifest


def test_main_passes_manifest_to_cross_build(
    main_module: ModuleType,
    patch_common_main_deps: Harness,
    setup_manifest: Path,
) -> None:
    harness = patch_common_main_deps
    target = "aarch64-unknown-linux-gnu"
    harness.patch_attr("_resolve_target_argument", lambda value: target)
    harness.patch_attr("_resolve_toolchain", lambda *_: ("1.89.0", ["1.89.0"]))
    decision = _cross_decision(main_module, use_cross=True)
    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

    captured: dict[str, object] = {}

    def fake_cross(
        decision_arg: object, target_arg: str, manifest_arg: Path
    ) -> _DummyCommand:
        captured["decision"] = decision_arg
        captured["target"] = target_arg
        captured["manifest"] = manifest_arg
        return _DummyCommand("cross-build")

    harness.patch_attr("_build_cross_command", fake_cross)
    harness.patch_attr("_build_cargo_command", _unexpected("unexpected cargo build"))

    main_module.main(target, toolchain="1.89.0")

    assert captured["manifest"] == setup_manifest


def test_main_passes_manifest_to_cargo_build(
    main_module: ModuleType,
    patch_common_main_deps: Harness,
    setup_manifest: Path,
) -> None:
    harness = patch_common_main_deps
    target = "x86_64-unknown-linux-gnu"
    harness.patch_attr("_resolve_target_argument", lambda value: value)
    decision = _cross_decision(main_module, use_cross=False)
    harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

    captured: dict[str, object] = {}

    def fake_cargo(spec: str, target_arg: str, manifest_arg: Path) -> _DummyCommand:
        captured["target"] = target_arg
        captured["manifest"] = manifest_arg
        return _DummyCommand("cargo-build")

    harness.patch_attr("_build_cargo_command", fake_cargo)
    harness.patch_attr("_build_cross_command", _unexpected("unexpected cross build"))

    main_module.main(target, toolchain="stable")

    assert captured["manifest"] == Path("Cargo.toml")


def test_main_errors_when_manifest_missing(
    main_module: ModuleType,
    patch_common_main_deps: Harness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = patch_common_main_deps
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)

    target = "x86_64-unknown-linux-gnu"
    harness.patch_attr("_resolve_target_argument", lambda value: target)
    decision = _cross_decision(main_module, use_cross=False)
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
    main_module: ModuleType, tmp_path: Path, builder: str, target: str
) -> None:
    manifest = (tmp_path / "Cargo.toml").resolve()
    if builder == "cross":
        decision = _cross_decision(main_module, use_cross=True)
        cmd = main_module._build_cross_command(decision, target, manifest)
    else:
        cmd = main_module._build_cargo_command("+stable", target, manifest)

    assert_manifest_in_command(cmd, manifest)


def test_handle_cross_container_error_passes_manifest_to_fallback(
    main_module: ModuleType,
    module_harness: HarnessFactory,
    tmp_path: Path,
) -> None:
    harness: Harness = module_harness(main_module)
    manifest = (tmp_path / "Cargo.toml").resolve()
    captured: dict[str, object] = {}

    def fake_cargo(spec: str, target_arg: str, manifest_arg: Path) -> _DummyCommand:
        captured["target"] = target_arg
        captured["manifest"] = manifest_arg
        return _DummyCommand("fallback")

    harness.patch_attr("_build_cargo_command", fake_cargo)

    decision = _cross_decision(main_module, use_cross=True)
    exc = ProcessExecutionError(["cross"], 125, "", "")

    main_module._handle_cross_container_error(exc, decision, "aarch64", manifest)

    assert captured["manifest"] == Path("Cargo.toml")
    assert harness.calls
    assert harness.calls[0] == ["fallback"]
