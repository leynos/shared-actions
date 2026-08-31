"""Tests for the macos-package Setup uv manifest step."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_setup_uv_uses_bundled_latest_known_version() -> None:
    """Avoid remote version-manifest resolution when packaging on macOS."""
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    manifest = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    setup_step = next(
        step for step in manifest["runs"]["steps"] if step["name"] == "Setup uv"
    )

    assert setup_step["with"]["version"] == "latest-known"
