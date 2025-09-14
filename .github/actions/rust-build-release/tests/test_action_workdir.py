"""Tests for GoReleaser workdir in action.yml."""

from __future__ import annotations

from pathlib import Path


def test_action_uses_workdir() -> None:
    """Ensure GoReleaser runs from project dir."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    text = action.read_text()
    assert "workdir: ${{ inputs.project-dir }}" in text
