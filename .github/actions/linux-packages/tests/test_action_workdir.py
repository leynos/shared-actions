"""Structural tests for the linux-packages composite action definition."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def _load_action() -> dict[str, object]:
    action = Path(__file__).resolve().parents[1] / "action.yml"
    text = action.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def test_action_uses_workdir() -> None:
    """Ensure packaging runs from project dir."""
    data = _load_action()
    steps = data["runs"]["steps"]
    package_step = next(
        (step for step in steps if "package.py" in step.get("run", "")),
        None,
    )
    assert package_step is not None, "Packaging step not found"
    assert package_step.get("working-directory") == "${{ inputs.project-dir }}"


def test_action_performs_self_checkout() -> None:
    """Ensure the composite checks out its own repository copy."""
    data = _load_action()
    steps = data["runs"]["steps"]
    checkout_step = steps[0]
    assert checkout_step["uses"] == "actions/checkout@v4"
    checkout_with = checkout_step.get("with", {})
    assert checkout_with == {
        "repository": "${{ github.action_repository }}",
        "ref": "${{ github.action_ref }}",
        "path": "_self",
        "token": "${{ inputs.action-token || github.token }}",
    }


def test_action_uses_self_checkout_paths() -> None:
    """Ensure nested actions resolve inside the self checkout directory."""
    data = _load_action()
    steps = data["runs"]["steps"]
    install_step = next(
        (step for step in steps if step.get("name") == "Install nfpm"),
        None,
    )
    assert install_step is not None, "Install nfpm step not found"
    assert install_step["uses"] == "./_self/.github/actions/install-nfpm"


def test_action_install_step_resolves_from_external_checkout(tmp_path: Path) -> None:
    """Simulate remote workspace and confirm the install action is reachable."""
    data = _load_action()
    steps = data["runs"]["steps"]
    install_step = next(
        (step for step in steps if step.get("name") == "Install nfpm"),
        None,
    )
    assert install_step is not None, "Install nfpm step not found"
    repo_root = Path(__file__).resolve().parents[4]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Simulate the checkout step cloning the repository into "_self".
    checkout_path = workspace / "_self"
    shutil.copytree(
        repo_root,
        checkout_path,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "target"),
    )
    install_path = workspace / install_step["uses"]
    assert install_path.is_dir(), (
        "Install nfpm action should exist under _self checkout"
    )
