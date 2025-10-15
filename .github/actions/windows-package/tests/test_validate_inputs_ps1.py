"""Contract tests for the validate_inputs PowerShell helper."""

from __future__ import annotations

import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import RunResult, run_cmd

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

if POWERSHELL is None:  # pragma: no cover - exercised only when PowerShell is missing
    pytest.skip(
        "PowerShell is required to test validate_inputs.ps1",
        allow_module_level=True,
    )


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_inputs.ps1"


def _combined_stream(result: RunResult) -> str:
    """Return stdout and stderr concatenated for assertions."""
    return f"{result.stdout}\n{result.stderr}"


def _run_script(overrides: dict[str, str]) -> RunResult:
    """Invoke validate_inputs.ps1 applying the provided environment overrides."""
    env = os.environ.copy()
    env.update(overrides)
    ps_command = local[POWERSHELL]["-NoLogo", "-NoProfile", "-File", str(SCRIPT_PATH)]
    return typ.cast("RunResult", run_cmd(ps_command, method="run", env=env))


def test_requires_application_when_using_template() -> None:
    """Fail fast when neither application-path nor wxs-path has been supplied."""
    result = _run_script({"WXS_PATH": "", "APPLICATION_SPEC": ""})
    assert result.returncode != 0
    assert "provide 'application-path' when 'wxs-path' is omitted" in _combined_stream(
        result
    )


def test_accepts_application_spec_without_wxs_path() -> None:
    """Allow the built-in template when the application-path input is present."""
    result = _run_script({"APPLICATION_SPEC": r"dist\MyApp.exe"})
    assert result.returncode == 0


def test_accepts_wxs_path_without_application_spec() -> None:
    """Allow callers to provide only a wxs-path when supplying custom authoring."""
    result = _run_script({"WXS_PATH": r"installer\Package.wxs"})
    assert result.returncode == 0
