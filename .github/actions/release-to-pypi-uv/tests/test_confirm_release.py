"""Tests for confirm_release.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from shared_actions_conftest import REQUIRES_UV

from .test_determine_release import base_env


pytestmark = REQUIRES_UV


def run_confirm(tmp_path: Path, expected: str, confirm: str) -> subprocess.CompletedProcess[str]:
    """Execute the confirmation script with the provided values.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory used as the working directory for the script.
    expected : str
        Expected confirmation string supplied via environment variable.
    confirm : str
        Confirmation value provided to the workflow input.

    Returns
    -------
    subprocess.CompletedProcess[str]
        Result from invoking the script with ``uv run``.
    """
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
    """Accept when the confirmation matches the expected phrase.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="release v1.2.3")

    assert result.returncode == 0, result.stderr
    assert "Manual confirmation OK." in result.stdout


def test_confirmation_failure(tmp_path: Path) -> None:
    """Reject confirmation attempts with mismatched input.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    result = run_confirm(tmp_path, expected="release v1.2.3", confirm="nope")

    assert result.returncode == 1
    assert "Confirmation failed" in result.stderr
