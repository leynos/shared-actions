"""Shared helpers for rust-build-release tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def assert_no_toolchain_override(parts: list[str]) -> None:
    """Assert that a cross command does not inject a +toolchain override."""
    assert parts[1] == "build"  # noqa: S101
    assert all(not part.startswith("+") for part in parts[1:])  # noqa: S101


def load_action_manifest() -> dict[str, object]:
    """Return the parsed composite action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def find_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    """Return the named step, failing clearly when the manifest lacks it."""
    for step in steps:
        if step.get("name") == name:
            return step
    message = f"step '{name}' missing from action"
    raise AssertionError(message)


def export_rustflags_run_script() -> str:
    """Return the shell fragment that exports the caller's RUSTFLAGS."""
    steps: list[dict[str, object]] = load_action_manifest()["runs"]["steps"]
    run_script = find_step(steps, "Export caller RUSTFLAGS").get("run")
    assert isinstance(run_script, str), "export step has no run script"  # noqa: S101
    return run_script


def requires_bash() -> str:
    """Return a usable bash path or skip shell-fragment tests."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash
