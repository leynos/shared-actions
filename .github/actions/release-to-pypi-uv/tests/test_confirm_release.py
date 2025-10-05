"""Tests for confirm_release.py."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from plumbum import local
from shared_actions_conftest import REQUIRES_UV

from cmd_utils_importer import import_cmd_utils
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from cmd_utils import RunResult
else:
    RunResult = import_cmd_utils().RunResult

from .test_determine_release import base_env

pytestmark = REQUIRES_UV


def run_confirm(tmp_path: Path, expected: str, confirm: str) -> RunResult:
    """Run the ``confirm_release`` script with explicit confirmation inputs."""
    env = base_env(tmp_path)
    env["EXPECTED"] = expected
    env["INPUT_CONFIRM"] = confirm
    script = Path(__file__).resolve().parents[1] / "scripts" / "confirm_release.py"
    command = local["uv"]["run", "--script", str(script)]
    return run_plumbum_command(command, method="run", env=env, cwd=env.get("PWD"))


def test_confirmation_success(tmp_path: Path) -> None:
    """Accept matching confirmation phrases."""
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="release v1.2.3")

    assert result.returncode == 0, result.stderr
    assert "Manual confirmation OK." in result.stdout


def test_confirmation_failure(tmp_path: Path) -> None:
    """Reject confirmation attempts with mismatched input."""
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="nope")

    assert result.returncode == 1
    assert "Confirmation failed" in result.stderr
