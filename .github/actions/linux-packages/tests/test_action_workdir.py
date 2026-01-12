"""Structural tests for the linux-packages composite action definition."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def _load_action() -> dict[str, object]:
    action = Path(__file__).resolve().parents[1] / "action.yml"
    text = action.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        git_path = candidate / ".git"
        if git_path.is_dir() or git_path.is_file():
            return candidate
    message = "Could not locate repository root from test file"
    raise AssertionError(message)


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


def test_action_mirrors_repository_into_workspace() -> None:
    """Ensure the composite copies its repository snapshot locally."""
    data = _load_action()
    steps = data["runs"]["steps"]
    mirror_step = steps[0]
    assert mirror_step["name"] == "Mirror action repository into workspace"
    assert mirror_step["shell"] == "bash"
    script = mirror_step.get("run", "")
    assert "github.action_path" in script
    assert "rsync" in script
    assert "--exclude='_self/'" in script
    assert "--exclude='target/'" in script
    assert "--exclude='.git/'" in script
    assert "tar cf -" in script
    assert "--exclude='./target'" in script
    assert "--exclude='./.git'" in script
    assert "_self" in script
    assert all(step.get("uses") != "actions/checkout@v4" for step in steps)


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
    repo_root = _find_repo_root(Path(__file__).resolve())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Simulate the mirroring step copying the repository into "_self".
    checkout_path = workspace / "_self"
    # Exclude version control, Python cache, build artefacts, and previous
    # mirror directories to match the action's copy behaviour.
    shutil.copytree(
        repo_root,
        checkout_path,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            "*.pyc",
            "target",
            "_self",
            ".cache",
            ".uv-cache",
        ),
    )
    install_path = workspace / install_step["uses"]
    assert install_path.is_dir(), (
        "Install nfpm action should exist under _self checkout"
    )
