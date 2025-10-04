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


def test_run_validated_invokes_run_completed_process(
    utils_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """run_validated delegates execution to cmd_utils.run_completed_process."""
    exe_path = tmp_path / "docker.exe"
    exe_path.write_text("", encoding="utf-8")

    captured_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_completed_process(
        args: typ.Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_calls.append((list(args), dict(kwargs)))
        return subprocess.CompletedProcess(tuple(args), 0, "ok", "")

    monkeypatch.setattr(
        utils_module, "run_completed_process", fake_run_completed_process
    )

    result = utils_module.run_validated(
        exe_path,
        ("info",),
        allowed_names=("docker", "docker.exe"),
        capture_output=True,
        check=True,
    )

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout == "ok"
    assert captured_calls == [
        (
            [str(exe_path), "info"],
            {
                "capture_output": True,
                "check": True,
                "text": True,
            },
        )
    ], "run_completed_process should be called with validated path and defaults"


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
