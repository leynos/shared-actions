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
    "steps.token-state.outputs.missing == 'true' && "
    "steps.gate-applicability.outputs.skip != 'true'"
)
TOKEN_PRESENT = "steps.token-state.outputs.missing != 'true'"  # noqa: S105 - an expression, not a secret.


def _step(name: str) -> dict[str, object]:
    """Return the composite step called *name* from ``action.yml``."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return next(s for s in manifest["runs"]["steps"] if s.get("name") == name)


def _run_notice(
    tmp_path: Path, mode: str = "upload"
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the notice step's body; return the process and the summary text."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    step = _step(NOTICE_STEP)
    summary = tmp_path / "summary.md"
    env = {"INPUT_MODE": mode}
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", str(step["run"])],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ | env | {"GITHUB_STEP_SUMMARY": str(summary)},
        text=True,
    )
    return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


@pytest.mark.parametrize(
    ("mode", "outcome"),
    [
        pytest.param("upload", "nothing was uploaded", id="upload"),
        pytest.param("check", "coverage was not checked", id="check"),
    ],
)
def test_an_empty_token_records_a_notice_and_succeeds(
    tmp_path: Path, mode: str, outcome: str
) -> None:
    """The annotation and the summary line both say what was not done."""
    message = f"No CodeScene access token is configured, so {outcome}."
    result, summary = _run_notice(tmp_path, mode)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"::notice title=CodeScene upload skipped::{message}"
    ]
    assert summary == f"CodeScene upload skipped: {message}\n"


def test_the_notice_step_runs_only_for_an_empty_token_outside_install() -> None:
    """The guard is exactly: token missing outside install mode, gate not skipped."""
    condition = " ".join(str(_step(NOTICE_STEP)["if"]).split())

    assert condition == EXPECTED_IF


def test_a_present_token_leaves_the_upload_unchanged() -> None:
    """The upload step still requires a non-empty token and upload mode."""
    condition = " ".join(str(_step(UPLOAD_STEP)["if"]).split())

    assert "inputs.access-token != ''" in condition
    assert "inputs.mode == 'upload'" in condition
    assert "inputs.access-token == ''" not in condition


@pytest.mark.parametrize(
    "name",
    [
        "Resolve trusted CodeScene CLI",
        "Cache CodeScene Coverage CLI",
        "Install CodeScene Coverage CLI",
        "Verify CodeScene Coverage CLI",
        "Add cs-coverage to PATH",
    ],
)
def test_cli_setup_is_skipped_for_an_empty_token_outside_install(name: str) -> None:
    """Without a token nothing needs the CLI, so setup cannot fail the skip."""
    condition = " ".join(str(_step(name)["if"]).split())

    assert TOKEN_PRESENT in condition


@pytest.mark.parametrize(
    ("mode", "has_token", "missing"),
    [
        ("upload", "false", True),
        ("check", "false", True),
        ("install", "false", False),
        ("upload", "true", False),
        ("check", "true", False),
    ],
)
def test_token_state_marks_only_a_missing_token_outside_install(
    tmp_path: Path, mode: str, has_token: str, *, missing: bool
) -> None:
    """`missing` is output exactly when a non-install mode has no token."""
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("bash integration tests need bash on a POSIX host")
    output = tmp_path / "output"
    step = _step("Detect a missing access token")
    subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", str(step["run"])],
        check=True,
        capture_output=True,
        env=os.environ
        | {"INPUT_MODE": mode, "HAS_TOKEN": has_token, "GITHUB_OUTPUT": str(output)},
        text=True,
    )

    written = output.read_text(encoding="utf-8") if output.exists() else ""
    assert (written == "missing=true\n") is missing, written
