"""Tests for manifest-path input wiring in the composite action."""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def _load_action_manifest() -> dict[str, object]:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _find_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    for step in steps:
        if step.get("name") == name:
            return step
    message = f"step '{name}' missing from action"
    raise AssertionError(message)


def test_manifest_path_input_declared() -> None:
    """The manifest-path input must exist with a Cargo.toml default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "manifest-path" in inputs
    manifest_input = inputs["manifest-path"]
    assert manifest_input.get("required") is False
    assert manifest_input.get("default") == "Cargo.toml"


def test_build_step_exports_manifest_path_env() -> None:
    """Build step should pass manifest-path via RBR_MANIFEST_PATH."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    build_step = _find_step(steps, "Build release")
    env = build_step.get("env")
    assert isinstance(env, dict)
    assert env.get("RBR_MANIFEST_PATH") == "${{ inputs.manifest-path }}"
