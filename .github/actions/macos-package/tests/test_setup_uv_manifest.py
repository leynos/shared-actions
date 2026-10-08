"""Tests for bundled ``latest-known`` resolution in the Setup uv step."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_setup_uv_uses_compatible_bundled_latest_known_resolution() -> None:
    """Use the setup-uv revision that resolves ``latest-known`` from its bundle."""
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    manifest = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    setup_step = next(
        step for step in manifest["runs"]["steps"] if step["name"] == "Setup uv"
    )

    assert (
        setup_step["uses"]
        == "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
    )
    assert setup_step["with"]["version"] == "latest-known"
