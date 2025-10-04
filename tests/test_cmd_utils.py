"""Tests for :mod:`cmd_utils`."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils import RunMethod, run_cmd


def _python_command(*args: str) -> object:
    command = local[sys.executable]
    return command[list(args)] if args else command


def test_run_cmd_returns_stdout_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    """run_cmd should return decoded stdout when using the default method."""
    script = "import sys; sys.stdout.write('hello')"
    result = run_cmd(_python_command("-c", script))

    assert result == "hello"
    echoed = capsys.readouterr()
    assert "$ " in echoed.out


@pytest.mark.parametrize("method", ["call", "run", "run_fg"], ids=lambda value: value)
def test_run_cmd_rejects_non_plumbum_inputs(method: RunMethod) -> None:
    """Passing non-plumbum objects should raise :class:`TypeError`."""
    with pytest.raises(TypeError, match="plumbum command"):
        run_cmd(object(), method=method)


def test_run_cmd_run_method_returns_process_tuple() -> None:
    """The run method should surface plumbum's return tuple."""
    script = "import sys; sys.stdout.write('world'); sys.stderr.write('!')"
    returncode, stdout, stderr = run_cmd(  # type: ignore[misc]
        _python_command("-c", script),
        method="run",
    )

    assert returncode == 0
    assert stdout == "world"
    assert stderr == "!"


def test_run_cmd_propagates_process_execution_error() -> None:
    """run_cmd should propagate plumbum's ProcessExecutionError."""
    with pytest.raises(ProcessExecutionError) as excinfo:
        run_cmd(_python_command("-c", "import sys; sys.exit(3)"))

    exc: ProcessExecutionError = excinfo.value
    assert exc.retcode == 3


def test_run_cmd_merges_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamic environment changes should be visible to executed commands."""
    monkeypatch.setenv("CMD_UTILS_TOKEN", "runtime")
    script = "import os; import sys; sys.stdout.write(os.environ['CMD_UTILS_TOKEN'])"

    result = run_cmd(_python_command("-c", script))

    assert result == "runtime"


def test_run_cmd_supports_explicit_environment_overrides() -> None:
    """The env parameter should override values when provided."""
    script = "import os; import sys; sys.stdout.write(os.environ['CMD_UTILS_TOKEN'])"
    command = _python_command("-c", script)

    result = run_cmd(command, env={"CMD_UTILS_TOKEN": "override"})

    assert result == "override"


def test_run_cmd_env_allows_variable_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Providing env should replace the inherited environment entirely."""
    monkeypatch.setenv("CMD_UTILS_TOKEN", "runtime")
    script = (
        "import os; import sys; sys.stdout.write(str('CMD_UTILS_TOKEN' in os.environ))"
    )
    command = _python_command("-c", script)

    sanitized_env = {
        key: value for key, value in os.environ.items() if key != "CMD_UTILS_TOKEN"
    }

    result = run_cmd(command, env=sanitized_env)

    assert result == "False"


def test_run_cmd_run_fg_streams_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Foreground execution should stream to the real stdout."""
    script = "import sys; sys.stdout.write('fg-test')"

    result = run_cmd(_python_command("-c", script), method="run_fg")

    assert result is None
    captured = capsys.readouterr()
    assert "fg-test" in captured.out


def test_run_cmd_rejects_unknown_method() -> None:
    """Unknown execution strategies should raise :class:`ValueError`."""
    command = _python_command("-c", "print('noop')")

    with pytest.raises(ValueError, match="Unknown run method"):
        run_cmd(command, method=typ.cast("RunMethod", "unknown"))
