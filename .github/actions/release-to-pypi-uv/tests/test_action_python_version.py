"""Tests covering the python-version input in action.yml."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml


def _load_action() -> dict[str, typ.Any]:
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    return yaml.safe_load(action_path.read_text(encoding="utf-8"))


def test_action_exposes_python_version_input() -> None:
    """Unit test: ensure metadata defines python-version with the expected default."""
    data = _load_action()
    python_version = data["inputs"]["python-version"]
    assert python_version["default"] == "3.13"
    assert "Python version" in python_version["description"]


def test_setup_step_forwards_python_version_input() -> None:
    """Behavioral test: ensure setup-uv installs the requested interpreter."""
    data = _load_action()
    steps = data["runs"]["steps"]
    setup_step = next(step for step in steps if step["name"] == "Setup uv")
    assert setup_step["with"]["python-version"] == "${{ inputs.python-version }}"


def test_install_step_uses_python_version_input() -> None:
    """Behavioral test: ensure uv python install receives the requested version."""
    data = _load_action()
    steps = data["runs"]["steps"]
    install_step = next(step for step in steps if step["name"] == "Install Python")
    assert 'uv python install "${{ inputs.python-version }}"' in install_step["run"]


def test_validate_step_passes_fail_on_empty_flag() -> None:
    """Ensure the validation step forwards the fail-on-empty input."""
    data = _load_action()
    steps = data["runs"]["steps"]
    validate_step = next(
        step for step in steps if step["name"] == "Validate TOML files"
    )
    env = validate_step["env"]
    assert env["INPUT_FAIL_ON_EMPTY"] == "${{ inputs.fail-on-empty }}"


def test_validate_step_passes_skip_directories_input() -> None:
    """Ensure the validation step forwards the skip-directories input."""
    data = _load_action()
    steps = data["runs"]["steps"]
    validate_step = next(
        step for step in steps if step["name"] == "Validate TOML files"
    )
    env = validate_step["env"]
    assert env["INPUT_SKIP_DIRECTORIES"] == "${{ inputs.skip-directories }}"
