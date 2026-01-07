"""Tests for the features parameter in the rust-build-release action."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from types import ModuleType

import pytest
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from pathlib import Path


class Harness(typ.Protocol):
    """Protocol describing the minimal harness interface used in tests."""

    calls: list[list[str]]

    def patch_attr(self, name: str, value: object) -> None:
        """Patch an attribute on the wrapped module."""


HarnessFactory = cabc.Callable[[ModuleType], Harness]


class _DummyCommand:
    def __init__(self, name: str = "dummy") -> None:
        self._name = name

    def formulate(self) -> list[str]:
        return [self._name]

    def __call__(self, *args: object, **kwargs: object) -> None:
        return None


def _cross_decision(main_module: ModuleType, *, use_cross: bool) -> object:
    return main_module._CrossDecision(
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
        requires_cross_container=False,
    )


@pytest.fixture
def setup_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a temporary Cargo manifest and switch into its directory."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)
    return manifest


@pytest.fixture
def patch_common_main_deps(
    main_module: ModuleType, module_harness: HarnessFactory
) -> Harness:
    """Provide a harness with the common main() dependencies patched."""
    harness: Harness = module_harness(main_module)
    harness.patch_attr("_ensure_rustup_exec", lambda: "/usr/bin/rustup")
    harness.patch_attr("_resolve_toolchain", lambda *_: ("stable", ["stable"]))
    harness.patch_attr("_ensure_target_installed", lambda *_: True)
    harness.patch_attr("configure_windows_linkers", lambda *_, **__: None)
    harness.patch_attr("_configure_cross_container_engine", lambda *_: (None, None))
    harness.patch_attr("_restore_container_engine", lambda *_, **__: None)
    return harness


class TestBuildCargoCommand:
    """Tests for the _build_cargo_command helper with features."""

    @pytest.mark.parametrize(
        ("features", "expected_in_parts", "expected_value"),
        [
            pytest.param("", False, None, id="without_features"),
            pytest.param("verbose", True, "verbose", id="single_feature"),
            pytest.param(
                "verbose,experimental",
                True,
                "verbose,experimental",
                id="multiple_features",
            ),
        ],
    )
    def test_cargo_command_features(
        self,
        main_module: ModuleType,
        tmp_path: Path,
        features: str,
        expected_in_parts: bool,  # noqa: FBT001
        expected_value: str | None,
    ) -> None:
        """Cargo command handles --features flag based on features input."""
        manifest = tmp_path / "Cargo.toml"
        target = "x86_64-unknown-linux-gnu"

        cmd = main_module._build_cargo_command("+stable", target, manifest, features)
        parts = list(cmd.formulate())

        if expected_in_parts:
            assert "--features" in parts
            idx = parts.index("--features")
            assert parts[idx + 1] == expected_value
        else:
            assert "--features" not in parts


class TestBuildCrossCommand:
    """Tests for the _build_cross_command helper with features."""

    @pytest.mark.parametrize(
        ("features", "expected_in_parts", "expected_value"),
        [
            pytest.param("", False, None, id="without_features"),
            pytest.param("verbose", True, "verbose", id="with_features"),
        ],
    )
    def test_cross_command_features(
        self,
        main_module: ModuleType,
        tmp_path: Path,
        features: str,
        expected_in_parts: bool,  # noqa: FBT001
        expected_value: str | None,
    ) -> None:
        """Cross command handles --features flag based on features input."""
        manifest = tmp_path / "Cargo.toml"
        target = "aarch64-unknown-linux-gnu"
        decision = _cross_decision(main_module, use_cross=True)

        cmd = main_module._build_cross_command(decision, target, manifest, features)
        parts = list(cmd.formulate())

        if expected_in_parts:
            assert "--features" in parts
            idx = parts.index("--features")
            assert parts[idx + 1] == expected_value
        else:
            assert "--features" not in parts


class TestMainFeaturesIntegration:
    """Tests for features parameter integration in main()."""

    @pytest.mark.parametrize(
        ("use_cross", "features", "target"),
        [
            pytest.param(
                False,
                "verbose,test",
                "x86_64-unknown-linux-gnu",
                id="cargo_with_features",
            ),
            pytest.param(
                True,
                "experimental",
                "aarch64-unknown-linux-gnu",
                id="cross_with_features",
            ),
            pytest.param(
                False, "", "x86_64-unknown-linux-gnu", id="cargo_without_features"
            ),
        ],
    )
    def test_main_passes_features(
        self,
        main_module: ModuleType,
        patch_common_main_deps: Harness,
        setup_manifest: Path,
        use_cross: bool,  # noqa: FBT001
        features: str,
        target: str,
    ) -> None:
        """main() correctly passes features to the appropriate build command."""
        harness = patch_common_main_deps
        captured: dict[str, object] = {}

        decision = _cross_decision(main_module, use_cross=use_cross)
        harness.patch_attr("_resolve_target_argument", lambda _value: target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        if use_cross:

            def fake_cross(
                decision_arg: object,
                target_arg: str,
                manifest_arg: Path,
                features_arg: str,
            ) -> _DummyCommand:
                captured["decision"] = decision_arg
                captured["target"] = target_arg
                captured["manifest"] = manifest_arg
                captured["features"] = features_arg
                return _DummyCommand("cross-build")

            harness.patch_attr("_build_cross_command", fake_cross)
        else:

            def fake_cargo(
                _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
            ) -> _DummyCommand:
                captured["target"] = target_arg
                captured["manifest"] = manifest_arg
                captured["features"] = features_arg
                return _DummyCommand("cargo-build")

            harness.patch_attr("_build_cargo_command", fake_cargo)

        if features:
            main_module.main(target, toolchain="stable", features=features)
        else:
            main_module.main(target, toolchain="stable")

        assert captured["features"] == features


class TestFeaturesEnvVar:
    """Tests for the RBR_FEATURES environment variable."""

    def test_features_from_environment(
        self,
        main_module: ModuleType,
        patch_common_main_deps: Harness,
        setup_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Features can be set via RBR_FEATURES environment variable."""
        harness = patch_common_main_deps
        captured: dict[str, object] = {}
        target = "x86_64-unknown-linux-gnu"

        monkeypatch.setenv("RBR_FEATURES", "env-feature")

        decision = _cross_decision(main_module, use_cross=False)
        harness.patch_attr("_resolve_target_argument", lambda _value: target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> _DummyCommand:
            captured["features"] = features_arg
            return _DummyCommand("cargo-build")

        harness.patch_attr("_build_cargo_command", fake_cargo)

        # Invoke via typer app to properly read environment variables
        from typer.testing import CliRunner

        runner = CliRunner()
        runner.invoke(main_module.app, [target, "--toolchain", "stable"])

        # The environment variable should be used - check captured value
        assert captured.get("features") == "env-feature"


class TestHandleCrossContainerErrorFeatures:
    """Tests for features handling in cross container error fallback."""

    def test_handle_cross_container_error_passes_features_to_fallback(
        self,
        main_module: ModuleType,
        module_harness: HarnessFactory,
        tmp_path: Path,
    ) -> None:
        """Fallback cargo builds must see the features as well."""
        harness: Harness = module_harness(main_module)
        manifest = (tmp_path / "Cargo.toml").resolve()
        captured: dict[str, object] = {}

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> _DummyCommand:
            captured["target"] = target_arg
            captured["manifest"] = manifest_arg
            captured["features"] = features_arg
            return _DummyCommand("fallback")

        harness.patch_attr("_build_cargo_command", fake_cargo)

        decision = _cross_decision(main_module, use_cross=True)
        exc = ProcessExecutionError(["cross"], 125, "", "")

        main_module._handle_cross_container_error(
            exc, decision, "aarch64", manifest, "verbose,test"
        )

        assert captured["features"] == "verbose,test"
        assert harness.calls
        assert harness.calls[0] == ["fallback"]
