"""Tests for GoReleaser workdir in action.yml."""

from __future__ import annotations

from pathlib import Path
import yaml


def test_action_uses_workdir() -> None:
    """Ensure GoReleaser runs from project dir."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    text = action.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    steps = data["runs"]["steps"]
    goreleaser_step = next(
        (
            step
            for step in steps
            if step.get("uses", "").startswith("goreleaser/goreleaser-action@")
        ),
        None,
    )
    assert goreleaser_step is not None, "GoReleaser step not found"
    assert goreleaser_step.get("with", {}).get("workdir") == "${{ inputs.project-dir }}"
