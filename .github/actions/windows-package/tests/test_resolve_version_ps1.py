"""Contract tests for the resolve_version PowerShell helper."""

from __future__ import annotations

import os
import re
import shutil
import textwrap
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import RunResult, run_cmd

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

if POWERSHELL is None:  # pragma: no cover - exercised only when PowerShell is missing
    pytest.skip(
        "PowerShell is required to test resolve_version.ps1",
        allow_module_level=True,
    )


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_version.ps1"

_ANSI_ESCAPE = re.compile(r"\x1B\[[0-9;:]*[A-Za-z]")


def _invoke_get_msi_version(candidate: str) -> str | None:
    """Execute Get-MsiVersion with *candidate* and normalise the result."""
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
    ps_command = local[POWERSHELL]["-NoLogo", "-NoProfile", "-Command", command]
    result = typ.cast("RunResult", run_cmd(ps_command, method="run"))
    output = result.stdout.strip()
    return None if output == "__NULL__" else output


def _run_script(env: dict[str, str]) -> RunResult:
    """Invoke resolve_version.ps1 with the supplied environment overrides."""
    ps_command = local[POWERSHELL]["-NoLogo", "-NoProfile", "-File", str(SCRIPT_PATH)]
    return typ.cast("RunResult", run_cmd(ps_command, method="run", env=env))


def _strip_ansi(value: str) -> str:
    """Remove ANSI escape sequences from *value* for reliable assertions."""
    return _ANSI_ESCAPE.sub("", value)


def _combined_stream(result: RunResult) -> str:
    """Return stdout and stderr concatenated without colour control codes."""
    return _strip_ansi(f"{result.stdout}\n{result.stderr}")


@pytest.fixture
def script_runner(
    tmp_path: Path,
) -> cabc.Callable[[dict[str, str]], tuple[RunResult, Path]]:
    """Provide a helper that runs the script with custom environment variables."""

    def run_with_env(
        env_vars: dict[str, str],
    ) -> tuple[RunResult, Path]:
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
    """Return normalised version strings for accepted inputs."""
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
    """Reject version strings that fall outside MSI constraints."""
    assert _invoke_get_msi_version(candidate) is None


def test_script_honours_explicit_input_version(
    script_runner: cabc.Callable[[dict[str, str]], tuple[RunResult, Path]],
) -> None:
    """Prefer the explicit version input when provided."""
    result, output_file = script_runner({"INPUT_VERSION": "2.3.4"})
    assert result.returncode == 0
    assert "Resolved version (input): 2.3.4" in result.stdout
    assert output_file.read_text(encoding="utf-8").strip() == "version=2.3.4"


def test_script_warns_on_invalid_tag(
    script_runner: cabc.Callable[[dict[str, str]], tuple[RunResult, Path]],
) -> None:
    """Emit a warning and fall back to 0.0.0 for malformed tag versions."""
    result, output_file = script_runner(
        {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "release"}
    )
    assert result.returncode == 0
    assert "Tag 'release' does not match" in _combined_stream(result)
    assert output_file.read_text(encoding="utf-8").strip() == "version=0.0.0"


def test_script_errors_on_invalid_explicit_version(
    script_runner: cabc.Callable[[dict[str, str]], tuple[RunResult, Path]],
) -> None:
    """Fail fast when the explicit version input does not parse."""
    result, output_file = script_runner({"INPUT_VERSION": "invalid"})
    assert result.returncode != 0
    assert "Invalid MSI version 'invalid'" in _combined_stream(result)
    assert not output_file.exists()
