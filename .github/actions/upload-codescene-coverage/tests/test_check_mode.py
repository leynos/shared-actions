"""Behavioural contracts for CodeScene coverage check mode."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"


def _steps() -> list[dict[str, object]]:
    """Return the composite action steps."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _gate_applicability_step() -> dict[str, object]:
    """Return the check-mode gate-applicability step."""
    return next(step for step in _steps() if step.get("id") == "gate-applicability")


def _run_applicability_check(
    tmp_path: Path,
    *,
    base_ref: str,
    default_branch: str,
) -> subprocess.CompletedProcess[str]:
    """Execute the gate-applicability shell fragment."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    step = _gate_applicability_step()
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = os.environ | {
        "BASE_REF": base_ref,
        "DEFAULT_BRANCH": default_branch,
        "GITHUB_OUTPUT": str(output),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def _run_input_validation(
    tmp_path: Path,
    *,
    access_token: str,
    project_url: str,
) -> subprocess.CompletedProcess[str]:
    """Execute check-mode validation with the supplied required inputs."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    step = next(step for step in _steps() if step.get("name") == "Validate inputs")
    script = str(step["run"])
    script = script.replace("${{ inputs.format }}", "cobertura")
    script = script.replace("${{ inputs.mode }}", "check")
    script = script.replace("${{ inputs.access-token }}", access_token)
    script = script.replace("${{ inputs.project-url }}", project_url)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", script],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )


def _run_gate_check(
    tmp_path: Path,
    *,
    exit_status: int = 2,
) -> subprocess.CompletedProcess[str]:
    """Execute the gate shell fragment with a configurable CLI stub."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    step = next(
        step
        for step in _steps()
        if step.get("name") == "Check coverage against CodeScene gates"
    )
    script = str(step["run"])
    script = script.replace("${{ steps.cov-file.outputs.path }}", "coverage.xml")
    script = script.replace("${{ inputs.format }}", "cobertura")

    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    cli = tmp_path / "cs-coverage"
    stderr_diagnostic = (
        "printf 'detailed gate stderr diagnostic\\n' >&2\n" if exit_status else ""
    )
    cli.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'arguments: %s\\n' \"$*\"\n"
        "printf 'detailed gate diagnostic\\n'\n"
        f"{stderr_diagnostic}"
        f"exit {exit_status}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    env = os.environ | {
        "GITHUB_BASE_REF": "main",
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", script],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(
    ("base_ref", "default_branch"),
    [("feature-base", "main"), ("topic", "trunk")],
)
def test_stacked_pull_request_skips_gate_with_warning(
    tmp_path: Path,
    base_ref: str,
    default_branch: str,
) -> None:
    """A non-default pull request base is explicitly skipped."""
    result = _run_applicability_check(
        tmp_path,
        base_ref=base_ref,
        default_branch=default_branch,
    )

    assert result.returncode == 0
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == "skip=true\n"
    assert "::warning title=CodeScene coverage gate skipped::" in result.stdout
    assert base_ref in result.stdout
    assert default_branch in result.stdout


@pytest.mark.parametrize(
    ("base_ref", "default_branch"),
    [("", "main"), ("main", "main"), ("trunk", "trunk")],
)
def test_non_stacked_context_remains_applicable(
    tmp_path: Path,
    base_ref: str,
    default_branch: str,
) -> None:
    """A non-PR or default-branch context continues to the CodeScene gate."""
    result = _run_applicability_check(
        tmp_path,
        base_ref=base_ref,
        default_branch=default_branch,
    )

    assert result.returncode == 0
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == ""
    assert "::warning" not in result.stdout


def test_gate_applicability_runs_only_in_check_mode() -> None:
    """Upload mode cannot enter the check-mode applicability boundary."""
    assert _gate_applicability_step()["if"] == "inputs.mode == 'check'"


def test_check_mode_rejects_an_empty_access_token(tmp_path: Path) -> None:
    """Check mode fails before installation when its token is unavailable."""
    result = _run_input_validation(
        tmp_path,
        access_token="",
        project_url="https://api.codescene.io/v2/projects/72004",
    )

    assert result.returncode == 1
    assert "mode: check requires a non-empty access-token" in result.stderr


def test_check_mode_rejects_a_missing_project_url(tmp_path: Path) -> None:
    """Check mode requires the CodeScene project endpoint before running."""
    result = _run_input_validation(
        tmp_path,
        access_token=os.environ["PATH"],
        project_url="",
    )

    assert result.returncode == 1
    assert "mode: check requires project-url" in result.stderr


def test_external_action_references_are_immutable() -> None:
    """Every nested third-party action uses a full immutable commit SHA."""
    for step in _steps():
        action_reference = str(step.get("uses", ""))
        if action_reference.startswith("actions/"):
            matcher = re.fullmatch(
                r"actions/[\w-]+@[0-9a-f]{40}(?:\s+#.*)?", action_reference
            )
            assert matcher, f"external action is not SHA-pinned: {action_reference}"


def test_skipped_gate_suppresses_all_following_steps() -> None:
    """Every step after the applicability decision honours its skip output."""
    steps = _steps()
    applicability_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("id") == "gate-applicability"
    )

    for step in steps[applicability_index + 1 :]:
        condition = str(step.get("if", ""))
        assert "steps.gate-applicability.outputs.skip != 'true'" in condition, step[
            "name"
        ]


def test_gate_success_streams_verbose_diagnostic(
    tmp_path: Path,
) -> None:
    """A successful CLI check streams its verbose diagnostic to stdout."""
    result = _run_gate_check(tmp_path, exit_status=0)

    assert result.returncode == 0
    assert "arguments: check --verbose --coverage-files coverage.xml" in result.stdout
    assert "detailed gate diagnostic" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("exit_status", [1, 2, 7])
def test_gate_failure_streams_diagnostic_and_preserves_status(
    tmp_path: Path,
    exit_status: int,
) -> None:
    """A failed CLI check streams details and handles missing baselines."""
    result = _run_gate_check(tmp_path, exit_status=exit_status)

    assert "arguments: check --verbose --coverage-files coverage.xml" in result.stdout
    assert "detailed gate diagnostic" in result.stdout
    assert "detailed gate stderr diagnostic" in result.stderr
    hint = "pull request base 'main' must have coverage uploaded"
    if exit_status == 2:
        assert result.returncode == 0
        warning = "::warning title=CodeScene coverage gate skipped::"
        warning_output = result.stdout if warning in result.stdout else result.stderr
        assert warning in warning_output
        assert "main" in warning_output
        assert "coverage baseline" in warning_output
    else:
        assert result.returncode == exit_status
        assert hint not in result.stderr


def test_successful_gate_emits_no_skip_warning(tmp_path: Path) -> None:
    """A successful CLI check does not emit a skipped-gate warning."""
    result = _run_gate_check(tmp_path, exit_status=0)

    assert result.returncode == 0
    assert "::warning title=CodeScene coverage gate skipped::" not in result.stdout
    assert "::warning title=CodeScene coverage gate skipped::" not in result.stderr
