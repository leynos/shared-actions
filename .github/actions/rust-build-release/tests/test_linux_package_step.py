"""Ensure the composite action leaves Linux packaging to workflows."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_linux_packaging_not_present() -> None:
    """Validate that linux packaging is no longer invoked by the action."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    data = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps = data["runs"]["steps"]

    assert all("linux-packages" not in (step.get("uses") or "") for step in steps), (
        "linux-packages step should be removed from rust-build-release"
    )
