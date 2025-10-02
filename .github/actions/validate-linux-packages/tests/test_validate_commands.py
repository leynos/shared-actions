"""Tests for the validate_commands helper module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from plumbum.commands.processes import ProcessExecutionError

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_commands.py"


@pytest.fixture(scope="module")
def validate_commands_module() -> object:
    """Load the validate_commands module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_commands")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_commands", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_commands module"
        raise RuntimeError(message)

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validation_error() -> type[Exception]:
    """Return the ValidationError class used by the commands module."""
    spec = importlib.util.spec_from_file_location(
        "validate_exceptions", SCRIPTS_DIR / "validate_exceptions.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("unable to load validate_exceptions module")
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module.ValidationError


class _DummyCommand:
    def __init__(self, stdout: object, *, argv: tuple[str, ...] = ("tool",)) -> None:
        self._stdout = stdout
        self.argv = argv
        self.run_calls: list[dict[str, object]] = []

    def run(self, *, timeout: int | None = None) -> tuple[int, object, object]:
        self.run_calls.append({"timeout": timeout})
        return 0, self._stdout, ""


def test_run_text_decodes_bytes(
    validate_commands_module: object,
) -> None:
    """run_text decodes byte output to strings."""
    command = _DummyCommand(b"hello\n")

    result = validate_commands_module.run_text(command, timeout=5)

    assert result == "hello\n"
    assert command.run_calls == [{"timeout": 5}]


def test_run_text_raises_for_process_error(
    validate_commands_module: object,
    validation_error: type[Exception],
) -> None:
    """Non-zero exit codes raise ValidationError with details."""

    class _FailingCommand:
        argv = ("tool", "--flag")

        def run(self, *, timeout: int | None = None) -> tuple[int, object, object]:
            raise ProcessExecutionError(self.argv, 2, "", "boom")

    with pytest.raises(validation_error) as excinfo:
        validate_commands_module.run_text(_FailingCommand())

    assert "command failed" in str(excinfo.value)
    assert "--flag" in str(excinfo.value)


def test_run_text_wraps_unexpected_errors(
    validate_commands_module: object,
    validation_error: type[Exception],
) -> None:
    """Unexpected exceptions are surfaced as ValidationError."""

    class _BrokenCommand:
        argv = ("tool",)

        def run(self, *, timeout: int | None = None) -> tuple[int, object, object]:
            raise RuntimeError("boom")

    with pytest.raises(validation_error) as excinfo:
        validate_commands_module.run_text(_BrokenCommand())

    assert "command execution failed" in str(excinfo.value)
