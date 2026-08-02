"""Shared helpers for the setup-rust manifest tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def load_steps() -> list[dict[str, object]]:
    """Load the composite action steps from the setup-rust manifest."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def get_step(step_name: str) -> dict[str, object]:
    """Return a named composite action step, failing clearly if it is absent."""
    steps = load_steps()
    step = next((step for step in steps if step.get("name") == step_name), None)
    assert step is not None, f"Missing setup-rust step: {step_name}"  # noqa: S101
    return step


def requires_bash() -> str:
    """Return a usable bash path or skip shell-fragment tests."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash
