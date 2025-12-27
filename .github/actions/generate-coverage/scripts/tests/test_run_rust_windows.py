"""Tests for the Windows cargo output pump helper."""

from __future__ import annotations

import importlib.util
import io
import typing as typ
from pathlib import Path
from types import ModuleType

import pytest
from syspath_hack import prepend_to_syspath

MODULE_PATH = Path(__file__).resolve().parent.parent / "run_rust.py"
SCRIPT_DIR = MODULE_PATH.parent
prepend_to_syspath(SCRIPT_DIR)

spec = importlib.util.spec_from_file_location("run_rust_module", MODULE_PATH)
if spec is None or spec.loader is None:  # pragma: no cover - defensive import guard
    load_error_message = "Unable to load run_rust module for testing"
    raise RuntimeError(load_error_message)
run_rust_module = importlib.util.module_from_spec(spec)
if not isinstance(run_rust_module, ModuleType):  # pragma: no cover - importlib contract
    type_error_message = "module_from_spec did not return a ModuleType"
    raise TypeError(type_error_message)
spec.loader.exec_module(run_rust_module)  # type: ignore[misc]
run_rust = run_rust_module


class _SupportsKillWait(typ.Protocol):
    """Minimal protocol for processes that expose kill/wait."""

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = ...) -> int: ...


class _RunRustModule(typ.Protocol):
    """Subset of the run_rust module used by the tests."""

    sys: typ.Any

    def _pump_cargo_output_windows(
        self,
        proc: _SupportsKillWait,
        stdout_stream: typ.IO[str],
        stderr_stream: typ.IO[str],
    ) -> list[str]:
        """Mirror of the helper under test."""


run_rust_typed: _RunRustModule = typ.cast("_RunRustModule", run_rust)


class _DummyProc:
    def __init__(self) -> None:
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def kill(self) -> None:  # pragma: no cover - exercised only on failure
        self.killed = True

    def wait(
        self, timeout: float | None = None
    ) -> int:  # pragma: no cover - failure path
        self.wait_timeouts.append(timeout)
        return 0


@pytest.mark.parametrize(
    ("stdout_payload", "stderr_payload", "expected"),
    [
        ("hello\r\nworld\r\n", "warn\r\n", ["hello", "world"]),
        ("single line\n", "", ["single line"]),
    ],
)
def test_pump_cargo_output_windows_streams(
    stdout_payload: str,
    stderr_payload: str,
    expected: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream cargo output through the Windows pump into captured buffers."""
    dummy_proc = _DummyProc()

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    run_rust_sys = run_rust_typed.sys
    monkeypatch.setattr(run_rust_sys, "stdout", captured_stdout)
    monkeypatch.setattr(run_rust_sys, "stderr", captured_stderr)

    stdout_stream = io.StringIO(stdout_payload)
    stderr_stream = io.StringIO(stderr_payload)

    lines = run_rust_typed._pump_cargo_output_windows(
        dummy_proc,
        stdout_stream,
        stderr_stream,
    )

    assert lines == expected
    assert captured_stdout.getvalue() == stdout_payload
    assert captured_stderr.getvalue() == stderr_payload
    assert not dummy_proc.killed
    assert dummy_proc.wait_timeouts == []
