"""Executable contracts for cold-runner CodeScene parser diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[4]
WORKFLOW = REPOSITORY / ".github/workflows/test-upload-codescene-coverage.yml"


def _parser_step() -> str:
    """Return the cold-runner parser proof shell program."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        item
        for item in workflow["jobs"]["cold-runner-contract"]["steps"]
        if item["name"] == "Verify resolved version and parse Slipcover reports"
    )
    return str(step["run"])


def _run_parser_proof(
    tmp_path: Path, output: str, status: int
) -> subprocess.CompletedProcess[str]:
    """Run the workflow's parser fragment against a deterministic CLI stub."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    cli = tmp_path / "cs-coverage"
    cli.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = version ]; then\n'
        "  echo 'cs-coverage version 1.0.101 "
        "(ca2b95180eff32b5072e81f20d718d7b747650be)'\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$CS_TEST_OUTPUT\"\n"
        'exit "$CS_TEST_STATUS"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    environment = os.environ | {
        "CS_ACCESS_TOKEN": "test-token",
        "CS_PROJECT_URL": "https://api.codescene.io/v2/projects/test",
        "CS_TEST_OUTPUT": output,
        "CS_TEST_STATUS": str(status),
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    }
    return subprocess.run(  # noqa: S603,TID251 - execute the workflow bash contract.
        [bash, "-c", _parser_step()],
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env=environment,
        text=True,
    )


def test_parser_proof_succeeds_only_with_pass_and_zero_status(tmp_path: Path) -> None:
    """A passing CLI result remains a successful cold-runner parser proof."""
    result = _run_parser_proof(tmp_path, "Code coverage gates: PASS", 0)

    assert result.returncode == 0
    assert "Code coverage gates: PASS" in result.stdout


@pytest.mark.parametrize("status", [1, 7])
def test_parser_proof_preserves_generic_cli_failure(
    tmp_path: Path, status: int
) -> None:
    """A non-parser CLI failure preserves its original exit status."""
    result = _run_parser_proof(tmp_path, "generic CLI failure", status)

    assert result.returncode == status
    assert "generic CLI failure" in result.stdout


def test_parser_proof_reports_the_known_error_before_failing(tmp_path: Path) -> None:
    """The known parser diagnostic is streamed before its status is returned."""
    diagnostic = "No matching field found: close for class java.io.InputStreamReader"
    result = _run_parser_proof(tmp_path, diagnostic, 8)

    assert result.returncode == 8
    assert diagnostic in result.stdout
    assert "cs-coverage 1.0.101 reported the known parser failure" in result.stderr


def test_parser_proof_rejects_a_zero_status_without_pass(tmp_path: Path) -> None:
    """A CLI that exits successfully without PASS cannot satisfy the contract."""
    result = _run_parser_proof(tmp_path, "inconclusive response", 0)

    assert result.returncode == 1
    assert "cs-coverage did not report a PASS result" in result.stderr


def test_action_reports_bounded_secret_free_outcomes() -> None:
    """Installer metrics reach the job summary without exposing caller secrets."""
    action = (
        REPOSITORY / ".github/actions/upload-codescene-coverage/action.yml"
    ).read_text(encoding="utf-8")

    for outcome in (
        "cs-coverage.resolve=verified",
        "cs-coverage.download=verified",
        "cs-coverage.digest=verified",
        "cs-coverage.install=verified",
        "cs-coverage.version=verified",
        "cs-coverage.install=failed",
    ):
        assert outcome in action
    assert "GITHUB_STEP_SUMMARY" in action
    manifest = yaml.safe_load(action)
    for name in (
        "Resolve trusted CodeScene CLI",
        "Install CodeScene Coverage CLI",
        "Verify CodeScene Coverage CLI",
    ):
        step = next(item for item in manifest["runs"]["steps"] if item["name"] == name)
        assert "CS_ACCESS_TOKEN" not in str(step["run"])
    assert "archive_url" not in action
