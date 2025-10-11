from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

if POWERSHELL is None:  # pragma: no cover - exercised only when PowerShell is missing
    pytest.skip("PowerShell is required to test resolve_version.ps1", allow_module_level=True)


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_version.ps1"


def _invoke_get_msi_version(candidate: str) -> str | None:
    literal = candidate.replace("'", "''")
    command = textwrap.dedent(
        f"""
        . '{SCRIPT_PATH}';
        $result = Get-MsiVersion('{literal}');
        if ($null -eq $result) {{
            Write-Output '__NULL__'
        }} else {{
            Write-Output $result
        }}
        """
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    output = completed.stdout.strip()
    return None if output == "__NULL__" else output


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def script_runner(
    tmp_path: Path,
) -> Callable[[dict[str, str]], tuple[subprocess.CompletedProcess[str], Path]]:
    """Fixture providing a helper to run the script with custom environment variables."""

    def run_with_env(env_vars: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], Path]:
        output_file = tmp_path / "output.txt"
        env = os.environ.copy()
        env["GITHUB_OUTPUT"] = str(output_file)
        env.update(env_vars)
        result = _run_script(env)
        return result, output_file

    return run_with_env


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("v1", "1.0.0"),
        ("V2", "2.0.0"),
        ("v1.2", "1.2.0"),
        ("1.2.3", "1.2.3"),
        ("  v01.02  ", "1.2.0"),
        ("3", "3.0.0"),
    ],
)
def test_get_msi_version_accepts_valid_inputs(candidate: str, expected: str) -> None:
    assert _invoke_get_msi_version(candidate) == expected


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "v1.2.3.4",
        "v1..2",
        "v1.2.-1",
        "256.0.0",
        "0.256.0",
        "v1.2.70000",
        "version",
    ],
)
def test_get_msi_version_rejects_invalid_inputs(candidate: str) -> None:
    assert _invoke_get_msi_version(candidate) is None


def test_script_honours_explicit_input_version(script_runner) -> None:
    result, output_file = script_runner({"INPUT_VERSION": "2.3.4"})
    assert result.returncode == 0
    assert "Resolved version (input): 2.3.4" in result.stdout
    assert output_file.read_text(encoding="utf-8").strip() == "version=2.3.4"


def test_script_warns_on_invalid_tag(script_runner) -> None:
    result, output_file = script_runner(
        {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "release"}
    )
    assert result.returncode == 0
    assert "Tag 'release' does not match" in result.stderr
    assert output_file.read_text(encoding="utf-8").strip() == "version=0.0.0"


def test_script_errors_on_invalid_explicit_version(script_runner) -> None:
    result, output_file = script_runner({"INPUT_VERSION": "invalid"})
    assert result.returncode != 0
    assert "Invalid MSI version 'invalid'" in result.stderr
    assert not output_file.exists()
