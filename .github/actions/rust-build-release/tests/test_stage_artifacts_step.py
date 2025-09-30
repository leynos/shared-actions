"""Tests covering the Stage artifacts step mapping."""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_stage_step() -> dict[str, object]:
    action = Path(__file__).resolve().parents[1] / "action.yml"
    data = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = data["runs"]["steps"]
    for step in steps:
        if step.get("id") == "stage-linux-artifacts":
            return step
    raise AssertionError("stage-linux-artifacts step missing from action")


def test_stage_step_condition_includes_illumos() -> None:
    """Validate illumos targets trigger the Stage artifacts step."""

    step = _load_stage_step()
    condition = step.get("if")
    assert isinstance(condition, str)
    assert "unknown-linux-" in condition
    assert "unknown-illumos" in condition


def test_stage_step_maps_illumos_to_expected_tuple() -> None:
    """Ensure the illumos target maps to illumos/amd64 output tuple."""

    step = _load_stage_step()
    run_script = step.get("run")
    assert isinstance(run_script, str)
    assert "x86_64-unknown-illumos" in run_script
    assert "os=illumos" in run_script
    assert "arch=amd64" in run_script
