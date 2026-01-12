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


@dataclasses.dataclass(frozen=True)
class CommandFeaturesContext:
    """Bundled dependencies for command feature tests."""

    main_module: ModuleType
    manifest: Path
    cross_decision: object


@dataclasses.dataclass(frozen=True)
class MainFeaturesContext:
    """Bundled dependencies for main() feature tests."""

    main_module: ModuleType
    harness: ModuleHarness
    cross_decision_factory: CrossDecisionFactory
    dummy_command_factory: DummyCommandFactory


@dataclasses.dataclass(frozen=True)
class CrossContainerContext:
    """Bundled dependencies for cross container fallback tests."""

    main_module: ModuleType
    harness: ModuleHarness
    manifest: Path
    decision: object
    dummy_command_factory: DummyCommandFactory


@pytest.fixture
def command_features_context(
    main_module: ModuleType,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
) -> CommandFeaturesContext:
    """Build a context for feature command tests."""
    return CommandFeaturesContext(
        main_module=main_module,
        manifest=tmp_path / "Cargo.toml",
        cross_decision=cross_decision_factory(main_module, use_cross=True),
    )


@pytest.fixture
def main_features_context(
    main_module: ModuleType,
    patch_common_main_deps: ModuleHarness,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> MainFeaturesContext:
    """Build a context for main() feature integration tests."""
    return MainFeaturesContext(
        main_module=main_module,
        harness=patch_common_main_deps,
        cross_decision_factory=cross_decision_factory,
        dummy_command_factory=dummy_command_factory,
    )


@pytest.fixture
def main_module_harness(
    main_module: ModuleType, module_harness: HarnessFactory
) -> ModuleHarness:
    """Return a module harness for the main module."""
    return module_harness(main_module)


@pytest.fixture
def cross_container_context(
    main_module_harness: ModuleHarness,
    tmp_path: Path,
    cross_decision_factory: CrossDecisionFactory,
    dummy_command_factory: DummyCommandFactory,
) -> CrossContainerContext:
    """Build a context for cross container fallback tests."""
    main_module = main_module_harness.module
    return CrossContainerContext(
        main_module=main_module,
        harness=main_module_harness,
        manifest=(tmp_path / "Cargo.toml").resolve(),
        decision=cross_decision_factory(main_module, use_cross=True),
        dummy_command_factory=dummy_command_factory,
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
        command_features_context: CommandFeaturesContext,
        builder_type: str,
        test_case: FeaturesTestCase,
    ) -> None:
        """Build commands handle --features flag correctly for cargo and cross."""
        context = command_features_context

        if builder_type == "cargo":
            target = "x86_64-unknown-linux-gnu"
            cmd = context.main_module._build_cargo_command(
                "+stable", target, context.manifest, test_case.features
            )
        else:
            target = "aarch64-unknown-linux-gnu"
            cmd = context.main_module._build_cross_command(
                context.cross_decision, target, context.manifest, test_case.features
            )

        parts = list(cmd.formulate())

        _assert_features_in_command_parts(
            parts, test_case.expected_in_parts, test_case.expected_value
        )


@pytest.mark.usefixtures("setup_manifest")
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
        main_features_context: MainFeaturesContext,
        test_case: MainFeaturesTestCase,
    ) -> None:
        """main() correctly passes features to the appropriate build command."""
        context = main_features_context
        harness = context.harness
        captured: dict[str, object] = {}

        decision = context.cross_decision_factory(
            context.main_module, use_cross=test_case.use_cross
        )
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
                return context.dummy_command_factory("cross-build")

            harness.patch_attr("_build_cross_command", fake_cross)
        else:

            def fake_cargo(
                _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
            ) -> object:
                captured["target"] = target_arg
                captured["manifest"] = manifest_arg
                captured["features"] = features_arg
                return context.dummy_command_factory("cargo-build")

            harness.patch_attr("_build_cargo_command", fake_cargo)

        context.main_module.main(
            test_case.target, toolchain="stable", features=test_case.features
        )

        assert captured["features"] == test_case.features


@pytest.mark.usefixtures("setup_manifest")
class TestFeaturesEnvVar:
    """Tests for the RBR_FEATURES environment variable."""

    def test_features_from_environment(
        self,
        main_features_context: MainFeaturesContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Features can be set via RBR_FEATURES environment variable."""
        context = main_features_context
        harness = context.harness
        captured: dict[str, object] = {}
        target = "x86_64-unknown-linux-gnu"

        monkeypatch.setenv("RBR_FEATURES", "env-feature")

        decision = context.cross_decision_factory(context.main_module, use_cross=False)
        harness.patch_attr("_resolve_target_argument", lambda _value: target)
        harness.patch_attr("_decide_cross_usage", lambda *_, **__: decision)

        def fake_cargo(
            _spec: str, target_arg: str, manifest_arg: Path, features_arg: str
        ) -> object:
            captured["features"] = features_arg
            return context.dummy_command_factory("cargo-build")

        harness.patch_attr("_build_cargo_command", fake_cargo)

        # Invoke via typer app to properly read environment variables
        from typer.testing import CliRunner

        runner = CliRunner()
        runner.invoke(context.main_module.app, [target, "--toolchain", "stable"])

        # The environment variable should be used - check captured value
        assert captured.get("features") == "env-feature"


class TestHandleCrossContainerErrorFeatures:
    """Tests for features handling in cross container error fallback."""

    def test_handle_cross_container_error_passes_features_to_fallback(
        self,
        cross_container_context: CrossContainerContext,
    ) -> None:
        """Fallback cargo builds must see the features as well."""
        context = cross_container_context
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
            exc,
            context.decision,
            "aarch64",
            context.manifest,
            "verbose,test",
        )

        assert captured["features"] == "verbose,test"
        assert harness.calls
        assert harness.calls[0] == ["fallback"]
