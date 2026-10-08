"""Tests for the windows-package composite action manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
WORKFLOW_PATH = Path(__file__).resolve().parents[3] / "workflows" / "rust-toy-app.yml"
CI_WORKFLOW_PATH = Path(__file__).resolve().parents[3] / "workflows" / "ci.yml"
CUSTOM_AUTHORING_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "custom-authoring" / "Package.wxs"
)


def _load_action_manifest() -> dict[str, object]:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def test_wix_extension_version_defaults_to_auto_match() -> None:
    """The action should auto-match the WiX extension major by default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    extension_version = inputs["wix-extension-version"]
    assert extension_version.get("default") == ""


def test_rust_toy_workflow_does_not_pin_stale_wix_extension_version() -> None:
    """The sample workflow should rely on the action default for WiX extensions."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    package_steps = jobs["build-release"]["steps"]
    build_windows_installer = next(
        step
        for step in package_steps
        if step.get("name") == "Build Windows installer package"
    )
    with_section = build_windows_installer["with"]
    assert "wix-extension-version" not in with_section


def test_windows_ci_compiles_custom_v4_authoring_with_the_supported_extension() -> None:
    """Compile caller-owned WiX v4 authoring through the Windows action lane."""
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python-tests-windows"]["steps"]
    validation_step = next(
        step for step in steps if step.get("name") == "Compile custom WiX v4 authoring"
    )
    with_section = validation_step["with"]

    assert validation_step["uses"] == "./.github/actions/windows-package"
    assert (
        with_section["wxs-path"]
        == ".github/actions/windows-package/tests/fixtures/custom-authoring/Package.wxs"
    )
    assert with_section["wix-extension-version"] == "7"
    assert with_section["upload-artefact"] == "false"
    assert CUSTOM_AUTHORING_PATH.exists()
    assert "http://wixtoolset.org/schemas/v4/wxs" in CUSTOM_AUTHORING_PATH.read_text(
        encoding="utf-8"
    )
