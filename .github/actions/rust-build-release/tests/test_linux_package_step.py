"""Ensure the composite action delegates Linux packaging to linux-packages."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_linux_packaging_step() -> None:
    """Validate the linux-packages step configuration."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    data = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps = data["runs"]["steps"]

    linux_step = next(
        (step for step in steps if "linux-packages" in step.get("uses", "")),
        None,
    )
    assert linux_step is not None, "linux-packages step missing"
    with_block = linux_step.get("with") or {}
    assert with_block.get("project-dir") == "${{ inputs.project-dir }}"
    assert with_block.get("bin-name") == "${{ inputs.bin-name }}"
    assert with_block.get("target") == "${{ inputs.target }}"
    assert with_block.get("version") == "${{ inputs.version }}"
    assert with_block.get("formats") == "${{ inputs.formats }}"
    assert "package-name" not in with_block
    assert (
        with_block.get("man-paths")
        == "${{ steps.stage-linux-artifacts.outputs.man-path }}"
    )
