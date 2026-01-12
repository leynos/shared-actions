"""Tests for the features parameter in the rust-build-release action."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from .conftest import (
        CrossDecisionFactory,
        DummyCommandFactory,
        HarnessFactory,
        ModuleHarness,
    )


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
                    features=" ", expected_in_parts=False, expected_value=None
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
                "cargo",
                FeaturesTestCase(
                    features="verbose , experimental",
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
                    features="  ", expected_in_parts=False, expected_value=None
                ),
            ),
            (
                "cross",
                FeaturesTestCase(
                    features="verbose", expected_in_parts=True, expected_value="verbose"
                ),
            ),
            (
                "cross",
                FeaturesTestCase(
                    features="verbose,experimental",
                    expected_in_parts=True,
                    expected_value="verbose,experimental",
                ),
            ),
            (
                "cross",
                FeaturesTestCase(
                    features="verbose , experimental",
                    expected_in_parts=True,
                    expected_value="verbose,experimental",
                ),
            ),
        ],
        ids=[
            "cargo_without_features",
            "cargo_whitespace_only",
            "cargo_single_feature",
            "cargo_multiple_features",
            "cargo_features_with_whitespace",
            "cross_without_features",
            "cross_whitespace_only",
            "cross_single_feature",
            "cross_multiple_features",
            "cross_features_with_whitespace",
        ],
    )
    def test_command_features(
        self,
        main_module: ModuleType,
        tmp_path: Path,
        builder_type: str,
        test_case: FeaturesTestCase,
        cross_decision_factory: CrossDecisionFactory,
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
            decision = cross_decision_factory(main_module, use_cross=True)
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
            MainFeaturesTestCase(
                use_cross=True,
                features="",
                target="aarch64-unknown-linux-gnu",
            ),
        ],
        ids=[
            "cargo_with_features",
            "cross_with_features",
            "cargo_without_features",
            "cross_without_features",
        ],
    )
    def test_main_passes_features(
        self,
        main_module: ModuleType,
        patch_common_main_deps: ModuleHarness,
        setup_manifest: Path,
        test_case: MainFeaturesTestCase,
        cross_decision_factory: CrossDecisionFactory,
        dummy_command_factory: DummyCommandFactory,
    ) -> None:
        """main() correctly passes features to the appropriate build command."""
        harness = patch_common_main_deps
        captured: dict[str, object] = {}

        decision = cross_decision_factory(main_module, use_cross=test_case.use_cross)
        harness.patch_attr("_resolve_target_argument", lambda _value: test_case.target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        if test_case.use_cross:

            def fake_cross(
                decision_arg: object,
                target_arg: str,
                manifest_arg: Path,
                features_arg: str,
            ) -> object:
                captured["decision"] = decision_arg
                captured["target"] = target_arg
                captured["manifest"] = manifest_arg
                captured["features"] = features_arg
                return dummy_command_factory("cross-build")

            harness.patch_attr("_build_cross_command", fake_cross)
        else:

            def fake_cargo(
                _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
            ) -> object:
                captured["target"] = target_arg
                captured["manifest"] = manifest_arg
                captured["features"] = features_arg
                return dummy_command_factory("cargo-build")

            harness.patch_attr("_build_cargo_command", fake_cargo)

        main_module.main(
            test_case.target, toolchain="stable", features=test_case.features
        )

        assert captured["features"] == test_case.features


class TestFeaturesEnvVar:
    """Tests for the RBR_FEATURES environment variable."""

    def test_features_from_environment(
        self,
        main_module: ModuleType,
        patch_common_main_deps: ModuleHarness,
        setup_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
        cross_decision_factory: CrossDecisionFactory,
        dummy_command_factory: DummyCommandFactory,
    ) -> None:
        """Features can be set via RBR_FEATURES environment variable."""
        harness = patch_common_main_deps
        captured: dict[str, object] = {}
        target = "x86_64-unknown-linux-gnu"

        monkeypatch.setenv("RBR_FEATURES", "env-feature")

        decision = cross_decision_factory(main_module, use_cross=False)
        harness.patch_attr("_resolve_target_argument", lambda _value: target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["features"] = features_arg
            return dummy_command_factory("cargo-build")

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
        cross_decision_factory: CrossDecisionFactory,
        dummy_command_factory: DummyCommandFactory,
    ) -> None:
        """Fallback cargo builds must see the features as well."""
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

        main_module._handle_cross_container_error(
            exc, decision, "aarch64", manifest, "verbose,test"
        )

        assert captured["features"] == "verbose,test"
        assert harness.calls
        assert harness.calls[0] == ["fallback"]
