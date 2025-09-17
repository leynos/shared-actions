"""Tests for packaging workdir in action.yml."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_action_uses_workdir() -> None:
    """Ensure packaging runs from project dir."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    text = action.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    steps = data["runs"]["steps"]
    package_step = next(
        (step for step in steps if "package.py" in step.get("run", "")),
        None,
    )
    assert package_step is not None, "Packaging step not found"
    assert package_step.get("working-directory") == "${{ inputs.project-dir }}"
