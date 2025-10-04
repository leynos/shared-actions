"""Tests for the validate_workspaces.py script."""

from __future__ import annotations

import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_completed_process

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_workspaces.py"
UV_NOT_FOUND_MESSAGE = "uv executable not found on PATH"


def run_validator(workspaces: str) -> subprocess.CompletedProcess[str]:
    """Execute the validator with *workspaces* and return the completed process."""
    env = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{os.pathsep}{current_pp}" if current_pp else root
    env["PYTHONIOENCODING"] = "utf-8"
    env["INPUT_WORKSPACES"] = workspaces
    uv_path = shutil.which("uv")
    if uv_path is None:
        pytest.skip(UV_NOT_FOUND_MESSAGE)
    command = local[uv_path]["run", "--script", str(SCRIPT_PATH)]
    return run_completed_process(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def test_accepts_empty_input() -> None:
    """Blank input is allowed and uses the default mapping."""
    result = run_validator("")
    assert result.returncode == 0
    assert result.stderr.strip() == ""


def test_accepts_valid_mappings() -> None:
    """Valid mappings pass through without errors."""
    mappings = "\n".join(
        [
            ". -> target",
            "# comment",
            "crate -> target/debug",
            ". -> target  # inline comment",
        ]
    )
    result = run_validator(mappings)
    assert result.returncode == 0
    assert result.stderr.strip() == ""


def test_requires_arrow_separator() -> None:
    """Missing separator raises an informative error."""
    result = run_validator("crate target")
    assert result.returncode == 1
    assert "missing '->'" in result.stderr


def test_requires_non_empty_workspace() -> None:
    """Workspace part must not be empty."""
    result = run_validator(" -> target")
    assert result.returncode == 1
    assert "empty workspace" in result.stderr


def test_requires_non_empty_target() -> None:
    """Target part must not be empty."""
    result = run_validator("crate ->   ")
    assert result.returncode == 1
    assert "empty target" in result.stderr
