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
    checkout_step = next(
        (step for step in steps if step.get("uses") == "actions/checkout@v4"),
        None,
    )
    assert checkout_step is not None, "Checkout step not found"
    checkout_with = checkout_step.get("with", {})
    assert checkout_with == {
        "repository": (
            "${{ env.SELF_REPO != '' && env.SELF_REPO || env.CALLER_REPO }}"
        ),
        "ref": "${{ env.SELF_REF != '' && env.SELF_REF || env.CALLER_REF }}",
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


def test_action_records_self_metadata_before_checkout() -> None:
    """Verify the metadata capture step exports repository and ref details."""
    data = _load_action()
    steps = data["runs"]["steps"]
    capture_step = steps[0]
    assert capture_step["name"] == "Capture action metadata"
    assert capture_step["shell"] == "bash"
    assert capture_step["env"] == {
        "SELF_REPO": "${{ github.action_repository }}",
        "SELF_REF": "${{ github.action_ref }}",
        "CALLER_REPO": "${{ github.repository }}",
        "CALLER_REF": "${{ github.ref_name || github.sha }}",
    }
    run_script = capture_step.get("run", "")
    assert "SELF_REPO=${SELF_REPO}" in run_script
    assert "SELF_REF=${SELF_REF}" in run_script
    assert "CALLER_REPO=${CALLER_REPO}" in run_script
    assert "CALLER_REF=${CALLER_REF}" in run_script
    assert "${GITHUB_ENV}" in run_script
