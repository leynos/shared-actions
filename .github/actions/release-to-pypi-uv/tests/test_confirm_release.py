"""Tests for confirm_release.py."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from shared_actions_conftest import REQUIRES_UV

from cmd_utils import run_cmd

from .test_determine_release import base_env

pytestmark = REQUIRES_UV


def run_confirm(tmp_path: Path, expected: str, confirm: str) -> tuple[int, str, str]:
    """Run the ``confirm_release`` script with explicit confirmation inputs."""
    env = base_env(tmp_path)
    env["EXPECTED"] = expected
    env["INPUT_CONFIRM"] = confirm
    script = Path(__file__).resolve().parents[1] / "scripts" / "confirm_release.py"
    command = local["uv"]["run", "--script", str(script)]
    try:
        code, stdout, stderr = typ.cast(
            "tuple[int, str | bytes, str | bytes]",
            run_cmd(
                command,
                method="run",
                env=env,
                cwd=env.get("PWD"),
            ),
        )
    except ProcessExecutionError as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return (
            int(exc.retcode),
            stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace"),
            stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace"),
        )
    return (
        code,
        stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout,
        stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr,
    )


def test_confirmation_success(tmp_path: Path) -> None:
    """Accept matching confirmation phrases."""
    returncode, stdout, stderr = run_confirm(
        tmp_path, expected="release v1.2.3", confirm="release v1.2.3"
    )

    assert returncode == 0, stderr
    assert "Manual confirmation OK." in stdout


def test_confirmation_failure(tmp_path: Path) -> None:
    """Reject confirmation attempts with mismatched input."""
    returncode, _, stderr = run_confirm(
        tmp_path, expected="release v1.2.3", confirm="nope"
    )

    assert returncode == 1
    assert "Confirmation failed" in stderr
