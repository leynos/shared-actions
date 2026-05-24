"""Tests covering the setup-rust composite action manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def _load_steps() -> list[dict[str, object]]:
    """Load the composite action steps from the setup-rust manifest."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _get_step(step_name: str) -> dict[str, object]:
    """Return a named composite action step, failing clearly if it is absent."""
    steps = _load_steps()
    step = next((step for step in steps if step.get("name") == step_name), None)
    assert step is not None, f"Missing setup-rust step: {step_name}"
    return step


def _install_binstall_run_script() -> str:
    """Return the cargo-binstall install step shell script."""
    install_step = _get_step("Install cargo-binstall")
    run_script = install_step.get("run")
    assert isinstance(run_script, str), "Install cargo-binstall step has no run script"
    return run_script


def _get_step_condition(step_name: str) -> str:
    """Return a named composite action step condition."""
    step = _get_step(step_name)
    condition = step.get("if")
    assert isinstance(condition, str), f"Step has no string condition: {step_name}"
    return condition


def test_manifest_exposes_toolchain_input() -> None:
    """The action should accept a toolchain override input."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    inputs = manifest.get("inputs", {})
    assert "toolchain" in inputs


def test_install_postgres_deps_is_linux_only() -> None:
    """Postgres packages should only install on Linux when requested."""
    condition = _get_step_condition("Install system dependencies")
    assert "runner.os == 'Linux'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition


def test_install_postgres_deps_windows_uses_choco() -> None:
    """Windows Postgres deps should install via Chocolatey when requested."""
    condition = _get_step_condition("Install libpq (headers + import library)")
    assert "runner.os == 'Windows'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition


def test_install_binstall_exports_version_pin() -> None:
    """The cargo-binstall installer should inherit the pinned version."""
    run_script = _install_binstall_run_script()
    run_lines = {line.strip() for line in run_script.splitlines()}
    assert 'export BINSTALL_VERSION="v1.16.6"' in run_lines


def test_install_binstall_verifies_installed_version() -> None:
    """The cargo-binstall step should assert the installed pinned version."""
    run_script = _install_binstall_run_script()
    run_lines = {line.strip() for line in run_script.splitlines()}
    assert 'if ! cargo-binstall -V | grep -q "1.16.6"; then' in run_lines
