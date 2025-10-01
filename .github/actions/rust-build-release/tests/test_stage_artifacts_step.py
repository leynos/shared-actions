"""Tests covering the Stage artifacts step mapping."""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_stage_step() -> dict[str, object]:
    action = Path(__file__).resolve().parents[1] / "action.yml"
    data = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = data["runs"]["steps"]
    for step in steps:
        if step.get("id") == "stage-artifacts":
            return step
    message = "stage-artifacts step missing from action"
    raise AssertionError(message)


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


def test_stage_step_preserves_existing_linux_mapping() -> None:
    """Verify existing Linux target mappings remain correct."""
    step = _load_stage_step()
    run_script = step.get("run")
    assert isinstance(run_script, str)
    assert "x86_64-unknown-linux-" in run_script
    assert "os=linux" in run_script
    lines = run_script.split("\n")
    in_x86_linux_case = False
    for line in lines:
        if "x86_64-unknown-linux-" in line:
            in_x86_linux_case = True
            continue
        if in_x86_linux_case and "arch=amd64" in line:
            break
        if in_x86_linux_case and line.strip() == ";;":
            message = "amd64 arch not found in x86_64 Linux case"
            raise AssertionError(message)


def test_stage_step_errors_for_unsupported_target() -> None:
    """Confirm unsupported targets trigger the default error path."""
    step = _load_stage_step()
    run_script = step.get("run")
    assert isinstance(run_script, str)
    assert "::error:: unsupported target" in run_script
    lines = run_script.split("\n")
    default_case_lines: list[str] = []
    in_default_case = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("*)"):
            in_default_case = True
            continue
        if in_default_case and stripped == ";;":
            break
        if in_default_case:
            default_case_lines.append(stripped)
    assert default_case_lines, "default case not found in stage script"
    assert any("::error:: unsupported target" in line for line in default_case_lines)
    assert any(line == "exit 1" for line in default_case_lines)
