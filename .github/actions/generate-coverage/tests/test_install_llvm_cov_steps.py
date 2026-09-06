"""Contracts for the cargo-llvm-cov install steps in both coverage actions.

The installer script is exercised by ``test_install_cargo_llvm_cov.py``; these
tests hold the action manifests to the step shape that invokes it: exactly one
``Install cargo-llvm-cov`` step per action, the generate-coverage one gated on
a Rust or mixed project, both running the manifest-driven script, no step
anywhere provisioning ``cargo-binstall``, and no cache path for it.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pytest
import yaml

ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2]
ACTIONS = {
    "generate-coverage": ACTIONS_DIR / "generate-coverage" / "action.yml",
    "ratchet-coverage": ACTIONS_DIR / "ratchet-coverage" / "action.yml",
}
INSTALL_STEP = "Install cargo-llvm-cov"
INSTALLER = "install_cargo_llvm_cov.py"


def _steps(action: str) -> list[dict[str, typ.Any]]:
    """Return the composite action's steps in order."""
    document = yaml.safe_load(ACTIONS[action].read_text(encoding="utf-8"))
    return list(document["runs"]["steps"])


def _install_steps(action: str) -> list[dict[str, typ.Any]]:
    """Return the steps named after the installer."""
    return [step for step in _steps(action) if step.get("name") == INSTALL_STEP]


@pytest.mark.parametrize("action", list(ACTIONS), ids=list(ACTIONS))
def test_exactly_one_install_step_runs_the_manifest_installer(action: str) -> None:
    """One step installs cargo-llvm-cov, and it runs the manifest-driven script."""
    steps = _install_steps(action)
    assert len(steps) == 1, f"{action} must have exactly one {INSTALL_STEP!r} step"
    run = str(steps[0].get("run", ""))
    assert "uv run --script" in run
    assert run.rstrip().endswith(f'/scripts/{INSTALLER}"'), run
    assert steps[0].get("shell") == "bash"


def test_generate_coverage_install_step_covers_rust_and_mixed_projects() -> None:
    """The install step runs for Rust and mixed projects, and only those."""
    condition = str(_install_steps("generate-coverage")[0].get("if", ""))
    assert "steps.detect.outputs.lang == 'rust'" in condition
    assert "steps.detect.outputs.lang == 'mixed'" in condition
    assert "use-cargo-nextest" not in condition, (
        "cargo-llvm-cov is needed whether or not nextest drives the tests"
    )


@pytest.mark.parametrize("action", list(ACTIONS), ids=list(ACTIONS))
def test_nothing_provisions_or_invokes_cargo_binstall(action: str) -> None:
    """No step is named for cargo-binstall and no run block calls it."""
    for step in _steps(action):
        assert "binstall" not in str(step.get("name", "")).lower(), step.get("name")
        assert "cargo binstall" not in str(step.get("run", "")), step.get("name")
        assert "cargo-binstall" not in str(step.get("run", "")), step.get("name")


def test_generate_coverage_cargo_cache_omits_cargo_binstall() -> None:
    """The Cargo cache no longer archives a binary nothing installs."""
    cache = next(
        step
        for step in _steps("generate-coverage")
        if step.get("name") == "Cache cargo artefacts"
    )
    paths = [
        line.strip() for line in str(cache["with"]["path"]).splitlines() if line.strip()
    ]
    assert "~/.cargo/bin/cargo-binstall" not in paths
    assert "~/.cargo/bin/cargo-llvm-cov" in paths
