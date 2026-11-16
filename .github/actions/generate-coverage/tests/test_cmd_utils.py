"""Tests for the :mod:`cmd_utils` helper."""

from __future__ import annotations

import subprocess
import sys

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

from cmd_utils_importer import import_cmd_utils

cmd_utils = import_cmd_utils()
run_cmd = cmd_utils.run_cmd


class _TimeoutCommand:
    """A command stub that raises a timeout from ``run``."""

    def with_env(self, **_: str) -> _TimeoutCommand:
        """Return this stub after accepting runtime environment overrides."""
        return self

    def formulate(self) -> list[str]:
        """Return a stable command line for timeout assertions."""
        return ["fake-command"]

    def run(self, **_: object) -> None:
        """Raise the timeout used to exercise Plumbum exception mapping."""
        timeout_message = "command timed out"
        raise TimeoutError(timeout_message)


def test_run_cmd_foreground_timeout() -> None:
    """Foreground commands honour the configured timeout."""
    sleeper = local[sys.executable]["-c", "import time; time.sleep(5)"]
    with pytest.raises(ProcessTimedOut):
        run_cmd(sleeper, method="run_fg", timeout=0.2)


def test_run_cmd_foreground_success() -> None:
    """Foreground commands still succeed without a timeout."""
    cmd = local[sys.executable]["-c", "print('ok')"]
    run_cmd(cmd, method="run_fg")


def test_run_cmd_normalizes_timeout_error() -> None:
    """A TimeoutError is mapped with Plumbum's message and argv contract."""
    with pytest.raises(ProcessTimedOut) as excinfo:
        run_cmd(_TimeoutCommand(), method="run", timeout=0.2)

    assert excinfo.value.args == ("command timed out", ["fake-command"])
    assert excinfo.value.argv == ["fake-command"]


@pytest.mark.parametrize(
    ("retcode", "expected"),
    [(None, 1), ("2", 2), ("unknown", 1)],
)
def test_process_errors_normalize_optional_exit_codes(
    retcode: int | str | None,
    expected: int,
) -> None:
    """Process error adapters always expose a valid integer exit code."""
    error = ProcessExecutionError(["fake-command"], retcode, "out", "err")

    run_result = cmd_utils.process_error_to_run_result(error)
    subprocess_error = cmd_utils.process_error_to_subprocess(error, _TimeoutCommand())

    assert run_result.returncode == expected
    assert isinstance(subprocess_error, subprocess.CalledProcessError)
    assert subprocess_error.returncode == expected
