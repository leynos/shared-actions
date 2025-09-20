"""Tests for utility helpers."""

from __future__ import annotations

import subprocess
import typing as typ
from pathlib import Path

import pytest

from shared_actions_conftest import CMD_MOX_UNSUPPORTED

if typ.TYPE_CHECKING:
    from types import ModuleType


def test_ensure_allowed_executable_accepts_valid_name(
    utils_module: ModuleType, tmp_path: Path
) -> None:
    """Paths matching allowed names are returned unchanged."""
    exe_path = tmp_path / "Rustup.EXE"
    exe_path.write_text("", encoding="utf-8")
    resolved = utils_module.ensure_allowed_executable(
        exe_path, ("rustup", "rustup.exe")
    )
    assert Path(resolved).name == "Rustup.EXE"


def test_ensure_allowed_executable_rejects_unknown(
    utils_module: ModuleType, tmp_path: Path
) -> None:
    """Unexpected executables raise :class:`UnexpectedExecutableError`."""
    exe_path = tmp_path / "malicious.exe"
    exe_path.write_text("", encoding="utf-8")
    with pytest.raises(utils_module.UnexpectedExecutableError):
        utils_module.ensure_allowed_executable(exe_path, ("rustup", "rustup.exe"))


@CMD_MOX_UNSUPPORTED
def test_run_validated_invokes_subprocess_with_validated_path(
    utils_module: ModuleType,
    cmd_mox,
) -> None:
    """run_validated executes subprocess.run with the validated executable."""
    exe_path = cmd_mox.environment.shim_dir / "docker.exe"
    spy = cmd_mox.spy("docker.exe").with_args("info").returns(stdout="ok")

    cmd_mox.replay()
    result = utils_module.run_validated(
        exe_path,
        ["info"],
        allowed_names=("docker", "docker.exe"),
        check=True,
        capture_output=True,
        text=True,
    )
    cmd_mox.verify()

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.args[0] == str(exe_path)
    assert result.stdout == "ok"
    assert spy.call_count == 1


def test_run_validated_raises_for_unexpected_executable(
    utils_module: ModuleType, tmp_path: Path
) -> None:
    """run_validated propagates unexpected executable errors."""
    exe_path = tmp_path / "bad.exe"
    exe_path.write_text("", encoding="utf-8")

    with pytest.raises(utils_module.UnexpectedExecutableError):
        utils_module.run_validated(
            exe_path,
            ["info"],
            allowed_names=("docker", "docker.exe"),
            capture_output=True,
            text=True,
        )
