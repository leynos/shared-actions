"""Tests for utility helpers."""

from __future__ import annotations

import subprocess
import typing as typ
from pathlib import Path

import pytest

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


def test_run_validated_invokes_subprocess_with_validated_path(
    utils_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_validated executes subprocess.run with the validated executable."""
    exe_path = tmp_path / "docker.exe"
    exe_path.write_text("", encoding="utf-8")

    recorded: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        recorded["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok")

    monkeypatch.setattr(utils_module.subprocess, "run", fake_run)

    result = utils_module.run_validated(
        exe_path,
        ["info"],
        allowed_names=("docker", "docker.exe"),
        check=True,
        capture_output=True,
        text=True,
    )

    assert recorded["cmd"][0] == str(exe_path)
    assert recorded["cmd"][1:] == ["info"]
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout == "ok"


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
