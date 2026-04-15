"""Tests for the windows-package composite action manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
WORKFLOW_PATH = Path(__file__).resolve().parents[3] / "workflows" / "rust-toy-app.yml"


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
