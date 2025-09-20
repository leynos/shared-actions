"""Tests for confirm_release.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from shared_actions_conftest import REQUIRES_UV

from .test_determine_release import base_env


pytestmark = REQUIRES_UV


def run_confirm(tmp_path: Path, expected: str, confirm: str) -> subprocess.CompletedProcess[str]:
    env = base_env(tmp_path)
    env["EXPECTED"] = expected
    env["INPUT_CONFIRM"] = confirm
    script = Path(__file__).resolve().parents[1] / "scripts" / "confirm_release.py"
    cmd = ["uv", "run", "--script", str(script)]
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        cwd=env.get("PWD"),
    )


def test_confirmation_success(tmp_path: Path) -> None:
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="release v1.2.3")

    assert result.returncode == 0, result.stderr
    assert "Manual confirmation OK." in result.stdout


def test_confirmation_failure(tmp_path: Path) -> None:
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="nope")

    assert result.returncode == 1
    assert "Confirmation failed" in result.stderr
