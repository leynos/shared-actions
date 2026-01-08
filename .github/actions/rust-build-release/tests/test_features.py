"""Tests for the features parameter in the rust-build-release action."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses
import typing as typ
from types import ModuleType

import pytest
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(frozen=True)
class FeaturesTestCase:
    """Test case for features parameter in build commands."""

    features: str
    expected_in_parts: bool
    expected_value: str | None

    @property
    def test_id(self) -> str:
        """Return a descriptive test ID based on features."""
        if not self.features:
            return "without_features"
        if "," in self.features:
            return "multiple_features"
        return "single_feature"


@dataclasses.dataclass(frozen=True)
class MainFeaturesTestCase:
    """Test case for main() features integration."""

    use_cross: bool
    features: str
    target: str


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


def _assert_features_in_command_parts(
    parts: list[str],
    expected_in_parts: bool,  # noqa: FBT001
    expected_value: str | None,
) -> None:
    """Assert that --features flag is present/absent with the correct value."""
    if expected_in_parts:
        assert "--features" in parts
        idx = parts.index("--features")
        assert parts[idx + 1] == expected_value
    else:
        assert "--features" not in parts


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


class TestBuildCommandFeatures:
    """Tests for build command features handling."""

    @pytest.mark.parametrize(
        ("builder_type", "test_case"),
        [
            (
                "cargo",
                FeaturesTestCase(
                    features="", expected_in_parts=False, expected_value=None
                ),
            ),
            (
                "cargo",
                FeaturesTestCase(
                    features="verbose", expected_in_parts=True, expected_value="verbose"
                ),
            ),
            (
                "cargo",
                FeaturesTestCase(
                    features="verbose,experimental",
                    expected_in_parts=True,
                    expected_value="verbose,experimental",
                ),
            ),
            (
                "cross",
                FeaturesTestCase(
                    features="", expected_in_parts=False, expected_value=None
                ),
            ),
            (
                "cross",
                FeaturesTestCase(
                    features="verbose", expected_in_parts=True, expected_value="verbose"
                ),
            ),
        ],
        ids=[
            "cargo_without_features",
            "cargo_single_feature",
            "cargo_multiple_features",
            "cross_without_features",
            "cross_with_features",
        ],
    )
    def test_command_features(
        self,
        main_module: ModuleType,
        tmp_path: Path,
        builder_type: str,
        test_case: FeaturesTestCase,
    ) -> None:
        """Build commands handle --features flag correctly for cargo and cross."""
        manifest = tmp_path / "Cargo.toml"

        if builder_type == "cargo":
            target = "x86_64-unknown-linux-gnu"
            cmd = main_module._build_cargo_command(
                "+stable", target, manifest, test_case.features
            )
        else:
            target = "aarch64-unknown-linux-gnu"
            decision = _cross_decision(main_module, use_cross=True)
            cmd = main_module._build_cross_command(
                decision, target, manifest, test_case.features
            )

        parts = list(cmd.formulate())

        _assert_features_in_command_parts(
            parts, test_case.expected_in_parts, test_case.expected_value
        )


class TestMainFeaturesIntegration:
    """Tests for features parameter integration in main()."""

    @pytest.mark.parametrize(
        "test_case",
        [
            MainFeaturesTestCase(
                use_cross=False,
                features="verbose,test",
                target="x86_64-unknown-linux-gnu",
            ),
            MainFeaturesTestCase(
                use_cross=True,
                features="experimental",
                target="aarch64-unknown-linux-gnu",
            ),
            MainFeaturesTestCase(
                use_cross=False,
                features="",
                target="x86_64-unknown-linux-gnu",
            ),
        ],
        ids=["cargo_with_features", "cross_with_features", "cargo_without_features"],
    )
    def test_main_passes_features(
        self,
        main_module: ModuleType,
        patch_common_main_deps: Harness,
        setup_manifest: Path,
        test_case: MainFeaturesTestCase,
    ) -> None:
        """main() correctly passes features to the appropriate build command."""
        harness = patch_common_main_deps
        captured: dict[str, object] = {}

        decision = _cross_decision(main_module, use_cross=test_case.use_cross)
        harness.patch_attr("_resolve_target_argument", lambda _value: test_case.target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        if test_case.use_cross:

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

        if test_case.features:
            main_module.main(
                test_case.target, toolchain="stable", features=test_case.features
            )
        else:
            main_module.main(test_case.target, toolchain="stable")

        assert captured["features"] == test_case.features


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
