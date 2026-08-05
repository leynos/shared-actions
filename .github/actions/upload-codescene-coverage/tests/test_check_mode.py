"""Behavioural contracts for CodeScene coverage check mode."""

from __future__ import annotations

import os
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
    """A failed CLI check streams details and retains its return code."""
    result = _run_gate_check(tmp_path, exit_status=exit_status)

    assert result.returncode == exit_status
    assert "arguments: check --verbose --coverage-files coverage.xml" in result.stdout
    assert "detailed gate diagnostic" in result.stdout
    assert "detailed gate stderr diagnostic" in result.stderr
    hint = "pull request base 'main' must have coverage uploaded"
    if exit_status == 2:
        assert hint in result.stderr
    else:
        assert hint not in result.stderr
