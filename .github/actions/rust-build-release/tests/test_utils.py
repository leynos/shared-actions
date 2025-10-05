"""Tests for utility helpers."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from cmd_utils import RunResult

if typ.TYPE_CHECKING:
    from types import ModuleType

    from cmd_utils import SupportsFormulate
else:  # pragma: no cover - typing helper fallback
    SupportsFormulate = typ.Any


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


def test_run_validated_invokes_run_cmd(
    utils_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """run_validated should delegate to cmd_utils.run_cmd."""
    exe_path = tmp_path / "docker.exe"
    exe_path.write_text("", encoding="utf-8")

    captured_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_cmd(
        cmd: SupportsFormulate,
        *,
        method: str = "call",
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> object:
        captured_calls.append(
            (
                list(cmd.formulate()),
                {"method": method, "env": env, "kwargs": dict(kwargs)},
            )
        )
        return RunResult(0, "ok", "")

    monkeypatch.setattr(utils_module, "run_cmd", fake_run_cmd)

    result = utils_module.run_validated(
        exe_path,
        ("info",),
        allowed_names=("docker", "docker.exe"),
        timeout=1.5,
        retcode=0,
    )

    assert result == RunResult(0, "ok", "")
    assert captured_calls == [
        (
            [str(exe_path), "info"],
            {
                "method": "run",
                "env": None,
                "kwargs": {"timeout": 1.5, "retcode": 0},
            },
        )
    ], "run_cmd should be called with validated path and forwarded arguments"


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
            method="run",
        )
