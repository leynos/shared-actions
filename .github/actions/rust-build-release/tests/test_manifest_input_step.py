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
    assert manifest_input.get("required", False) is False
    assert manifest_input.get("default") == "Cargo.toml"


def test_toolchain_input_declared() -> None:
    """The toolchain override input must exist with an empty default."""
    manifest = _load_action_manifest()
    inputs = manifest["inputs"]
    assert "toolchain" in inputs
    toolchain_input = inputs["toolchain"]
    assert toolchain_input.get("required", False) is False
    assert toolchain_input.get("default") == ""


def test_build_step_exports_manifest_path_env() -> None:
    """Build step should pass manifest-path via RBR_MANIFEST_PATH."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    build_step = _find_step(steps, "Build release")
    env = build_step.get("env")
    assert isinstance(env, dict)
    assert env.get("RBR_MANIFEST_PATH") == "${{ inputs.manifest-path }}"


def test_determine_toolchain_step_uses_project_lookup_inputs() -> None:
    """Toolchain lookup must run in project-dir and receive both override inputs."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    determine_step = _find_step(steps, "Determine toolchain")
    assert determine_step.get("working-directory") == "${{ inputs.project-dir }}"
    run_script = determine_step.get("run")
    assert isinstance(run_script, str)
    assert '--toolchain "${{ inputs.toolchain }}"' in run_script
    assert '--manifest-path "${{ inputs.manifest-path }}"' in run_script


def test_stage_artefacts_step_uses_stable_manpage_path() -> None:
    """Packaging should use the stable generated-man path, not Cargo build hashes."""
    manifest = _load_action_manifest()
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    stage_step = _find_step(steps, "Stage artefacts")
    run_script = stage_step.get("run")
    assert isinstance(run_script, str)
    assert (
        'man_path="target/generated-man/${{ inputs.target }}/release/'
        '${{ inputs.bin-name }}.1"'
    ) in run_script
    assert "release/build" not in run_script
    assert "man_matches" not in run_script
