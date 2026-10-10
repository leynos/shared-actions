"""Contracts for the step that records a skipped upload when no token is set.

The step's own ``run:`` body is executed in bash with the environment the
composite action gives it, so each test exercises the shipped script rather
than a copy. A present token must leave the step disabled and the upload step
as it was.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"
NOTICE_STEP = "Record skipped CodeScene upload"
UPLOAD_STEP = "Upload coverage to CodeScene"
EXPECTED_IF = (
    "inputs.mode != 'install' && inputs.access-token == '' && "
    "steps.gate-applicability.outputs.skip != 'true'"
)


def _step(name: str) -> dict[str, object]:
    """Return the composite step called *name* from ``action.yml``."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return next(s for s in manifest["runs"]["steps"] if s.get("name") == name)


def _run_notice(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the notice step's body; return the process and the summary text."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    step = _step(NOTICE_STEP)
    summary = tmp_path / "summary.md"
    env = {str(k): str(v) for k, v in step["env"].items()}  # type: ignore[union-attr]
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", str(step["run"])],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ | env | {"GITHUB_STEP_SUMMARY": str(summary)},
        text=True,
    )
    return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


def test_an_empty_token_records_a_notice_and_succeeds(tmp_path: Path) -> None:
    """The annotation and the summary line both say nothing was uploaded."""
    result, summary = _run_notice(tmp_path)

    assert result.returncode == 0, result.stderr
    notice = (
        "::notice title=CodeScene upload skipped::"
        "No CodeScene access token is configured, so nothing was uploaded."
    )
    assert result.stdout.splitlines() == [notice]
    assert summary == (
        "CodeScene upload skipped: "
        "No CodeScene access token is configured, so nothing was uploaded.\n"
    )


def test_the_notice_step_runs_only_for_an_empty_token_outside_install() -> None:
    """The guard is exactly: not install mode, empty token, gate not skipped."""
    condition = " ".join(str(_step(NOTICE_STEP)["if"]).split())

    assert condition == EXPECTED_IF


def test_a_present_token_leaves_the_upload_unchanged() -> None:
    """The upload step still requires a non-empty token and upload mode."""
    condition = " ".join(str(_step(UPLOAD_STEP)["if"]).split())

    assert "inputs.access-token != ''" in condition
    assert "inputs.mode == 'upload'" in condition
    assert "inputs.access-token == ''" not in condition
