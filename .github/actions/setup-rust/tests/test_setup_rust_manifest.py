"""Tests covering the setup-rust composite action manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def _load_steps() -> list[dict[str, object]]:
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def test_manifest_exposes_toolchain_input() -> None:
    """The action should accept a toolchain override input."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    inputs = manifest.get("inputs", {})
    assert "toolchain" in inputs


def test_install_postgres_deps_is_linux_only() -> None:
    """Postgres packages should only install on Linux when requested."""
    steps = _load_steps()
    install_step = next(
        step for step in steps if step.get("name") == "Install system dependencies"
    )
    condition = install_step.get("if")
    assert isinstance(condition, str)
    assert "runner.os == 'Linux'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition


def test_install_postgres_deps_windows_uses_choco() -> None:
    """Windows Postgres deps should install via Chocolatey when requested."""
    steps = _load_steps()
    windows_step = next(
        step
        for step in steps
        if step.get("name") == "Install libpq (headers + import library)"
    )
    condition = windows_step.get("if")
    assert isinstance(condition, str)
    assert "runner.os == 'Windows'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition
