"""Tests for :mod:`cmd_utils`."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

from cmd_utils_importer import import_cmd_utils

if typ.TYPE_CHECKING:
    from cmd_utils import (
        RunMethod as _RunMethod,
    )
    from cmd_utils import (
        RunResult as _RunResult,
    )
    from cmd_utils import (
        coerce_run_result as _coerce_run_result,
    )
    from cmd_utils import (
        process_error_to_run_result as _process_error_to_run_result,
    )
    from cmd_utils import (
        run_cmd as _run_cmd,
    )

_cmd_utils = import_cmd_utils()
run_cmd = typ.cast("_run_cmd", _cmd_utils.run_cmd)
coerce_run_result = typ.cast("_coerce_run_result", _cmd_utils.coerce_run_result)
process_error_to_run_result = typ.cast(
    "_process_error_to_run_result", _cmd_utils.process_error_to_run_result
)
RunResult = typ.cast("type[_RunResult]", _cmd_utils.RunResult)
RunMethod = typ.cast("_RunMethod", _cmd_utils.RunMethod)


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


def test_run_cmd_run_method_returns_run_result() -> None:
    """The run method should surface plumbum's output via :class:`RunResult`."""
    script = "import sys; sys.stdout.write('world'); sys.stderr.write('!')"
    result = run_cmd(_python_command("-c", script), method="run")
    assert isinstance(result, RunResult)
    assert result.returncode == 0
    assert result.stdout == "world"
    assert result.stderr == "!"


def test_run_cmd_propagates_process_execution_error() -> None:
    """run_cmd should propagate plumbum's ProcessExecutionError."""
    with pytest.raises(ProcessExecutionError) as excinfo:
        run_cmd(_python_command("-c", "import sys; sys.exit(3)"))

    exc: ProcessExecutionError = excinfo.value
    assert exc.retcode == 3
    assert exc.stdout == ""
    assert exc.stderr == ""


def test_run_cmd_run_method_honours_timeout() -> None:
    """run_cmd should honour timeout settings for the run strategy."""
    script = "import time; time.sleep(10)"

    with pytest.raises((ProcessTimedOut, TimeoutError)):
        run_cmd(
            _python_command("-c", script),
            method="run",
            timeout=0.01,
        )


def test_run_cmd_run_method_captures_stderr_on_failure() -> None:
    """The run method should expose stderr content on failure."""
    script = "import sys; sys.stderr.write('error message'); sys.exit(5)"

    result = run_cmd(
        _python_command("-c", script),
        method="run",
    )

    assert isinstance(result, RunResult)
    assert result.returncode == 5
    assert result.stdout == ""
    assert "error message" in result.stderr


def test_run_cmd_call_includes_stderr_in_exception() -> None:
    """ProcessExecutionError raised by call should include stderr."""
    script = "import sys; sys.stderr.write('diagnostic'); sys.exit(9)"

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_cmd(_python_command("-c", script))

    exc: ProcessExecutionError = excinfo.value
    assert exc.retcode == 9
    assert "diagnostic" in (exc.stderr or "")


def test_process_error_helpers_decode_output() -> None:
    """process_error_to_run_result converts binary payloads to text."""
    error = ProcessExecutionError(("cmd",), 5, b"hello", b"err")
    run_result = process_error_to_run_result(error)
    assert isinstance(run_result, RunResult)
    assert run_result == RunResult(5, "hello", "err")

    coerced = coerce_run_result((0, b"out", b"err"))
    assert coerced == RunResult(0, "out", "err")


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
