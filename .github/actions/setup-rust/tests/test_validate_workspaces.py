"""Tests for the validate_workspaces.py script."""

from __future__ import annotations

import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils_importer import import_cmd_utils
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from cmd_utils import RunResult
else:
    RunResult = import_cmd_utils().RunResult

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_workspaces.py"
UV_NOT_FOUND_MESSAGE = "uv executable not found on PATH"


def _clean_stderr(stderr: str) -> str:
    """Strip uv virtual environment warnings from *stderr*."""
    lines = [
        line
        for line in stderr.splitlines()
        if not line.startswith("warning: `VIRTUAL_ENV=")
    ]
    return "\n".join(lines)


def run_validator(workspaces: str) -> RunResult:
    """Execute the validator with *workspaces* and return the completed process."""
    env = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{os.pathsep}{current_pp}" if current_pp else root
    env["PYTHONIOENCODING"] = "utf-8"
    env["INPUT_WORKSPACES"] = workspaces
    env.pop("VIRTUAL_ENV", None)
    uv_path = shutil.which("uv")
    if uv_path is None:
        pytest.skip(UV_NOT_FOUND_MESSAGE)
    command = local[uv_path]["run", "--script", str(SCRIPT_PATH)]
    return run_plumbum_command(command, method="run", env=env)


def test_accepts_empty_input() -> None:
    """Blank input is allowed and uses the default mapping."""
    result = run_validator("")
    assert result.returncode == 0
    assert _clean_stderr(result.stderr).strip() == ""


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
    assert _clean_stderr(result.stderr).strip() == ""


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
