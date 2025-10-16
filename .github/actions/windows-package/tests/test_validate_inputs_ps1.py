"""Contract tests for the validate_inputs PowerShell helper."""

from __future__ import annotations

import os
import re
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


ACTION_ROOT_ERROR = "Unable to locate windows-package action root"


def _find_action_root(start: Path) -> Path:
    """Return the action root directory that contains the scripts folder."""
    for candidate in [start, *list(start.parents)]:
        scripts_dir = candidate / "scripts"
        if (scripts_dir / "validate_inputs.ps1").exists():
            return candidate
    raise FileNotFoundError(ACTION_ROOT_ERROR)


ACTION_ROOT = _find_action_root(Path(__file__).resolve())
SCRIPT_PATH = ACTION_ROOT / "scripts" / "validate_inputs.ps1"

ERROR_HINT = "provide 'application-path' when 'wxs-path' is omitted"
_ANSI_ESCAPE = re.compile(r"\x1B\[[0-9;:]*[A-Za-z]")


def _normalize(text: str) -> str:
    """Lowercase text, drop pipe gutters, and normalise whitespace for comparisons."""
    cleaned = text.replace("\r", "").replace("|", "")
    return " ".join(cleaned.split()).lower()


ERROR_HINT_NORMALIZED = _normalize(ERROR_HINT)


def combined_stream(result: RunResult) -> str:
    """Return stdout and stderr concatenated for assertions."""
    combined = f"{result.stdout}\n{result.stderr}"
    return _ANSI_ESCAPE.sub("", combined).replace("\r", "")


def run_script(
    overrides: dict[str, str] | None = None,
    *,
    unset: typ.Iterable[str] | None = None,
) -> RunResult:
    """Invoke validate_inputs.ps1 applying the provided environment overrides."""
    env = os.environ.copy()
    if unset is not None:
        for key in unset:
            env.pop(key, None)
    env = env | (overrides or {})
    ps_command = local[POWERSHELL]["-NoLogo", "-NoProfile", "-File", str(SCRIPT_PATH)]
    result = run_cmd(ps_command, method="run", env=env)
    if not isinstance(result, RunResult):  # pragma: no cover - defensive
        msg = "validate_inputs.ps1 must return a RunResult when invoked via run_cmd"
        raise TypeError(msg)
    return result


def assert_error_hint(result: RunResult) -> None:
    """Assert the validation message is present in the PowerShell error output."""
    normalised = _normalize(combined_stream(result))
    assert ERROR_HINT_NORMALIZED in normalised


def test_requires_application_when_using_template() -> None:
    """Fail fast with exit code 1 when neither application-path nor wxs-path is set."""
    result = run_script({"WXS_PATH": "", "APPLICATION_SPEC": ""})
    assert result.returncode == 1
    assert_error_hint(result)


def test_missing_application_spec_env_var() -> None:
    """Fail fast when APPLICATION_SPEC is undefined and wxs-path is empty."""
    result = run_script({"WXS_PATH": ""}, unset=["APPLICATION_SPEC"])
    assert result.returncode == 1
    assert_error_hint(result)


def test_missing_wxs_path_env_var() -> None:
    """Fail fast when WXS_PATH is undefined and application-path is empty."""
    result = run_script({"APPLICATION_SPEC": ""}, unset=["WXS_PATH"])
    assert result.returncode == 1
    assert_error_hint(result)


def test_accepts_both_inputs() -> None:
    """Allow callers to supply both custom authoring and an application spec."""
    result = run_script(
        {"WXS_PATH": r"installer\Package.wxs", "APPLICATION_SPEC": r"dist\MyApp.exe"}
    )
    assert result.returncode == 0


def test_whitespace_only_inputs_are_rejected() -> None:
    """Treat whitespace-only inputs as empty and emit the validation error."""
    result = run_script({"WXS_PATH": "  ", "APPLICATION_SPEC": "\t\n"})
    assert result.returncode == 1
    assert_error_hint(result)


def test_accepts_application_spec_without_wxs_path() -> None:
    """Allow the built-in template when the application-path input is present."""
    result = run_script({"APPLICATION_SPEC": r"dist\MyApp.exe"}, unset=["WXS_PATH"])
    assert result.returncode == 0


def test_accepts_wxs_path_without_application_spec() -> None:
    """Allow callers to provide only a wxs-path when supplying custom authoring."""
    result = run_script(
        {"WXS_PATH": r"installer\Package.wxs"},
        unset=["APPLICATION_SPEC"],
    )
    assert result.returncode == 0
