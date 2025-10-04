"""Tests for :mod:`cmd_utils` helpers."""

from __future__ import annotations

import subprocess
import sys

import pytest

from cmd_utils import run_completed_process


def test_run_completed_process_returns_bytes_by_default() -> None:
    """run_completed_process should return bytes when text mode is disabled."""
    script = "import sys; sys.stdout.buffer.write(b'hello')"
    result = run_completed_process(
        [sys.executable, "-c", script],
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == b"hello"
    assert result.stderr == b""


def test_run_completed_process_empty_args_raises_value_error() -> None:
    """run_completed_process should reject empty argument sequences."""
    with pytest.raises(ValueError, match="requires at least one argument"):
        run_completed_process([])


def test_run_completed_process_supports_text_mode() -> None:
    """Enabling text mode should decode stdout and stderr as strings."""
    script = "import sys; sys.stdout.write('hello'); sys.stderr.write('oops')"
    result = run_completed_process(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.stdout == "hello"
    assert result.stderr == "oops"


def test_run_completed_process_text_mode_with_encoding() -> None:
    """Explicit encodings should be honoured when decoding text output."""
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'caf\\xe9'); "
        "sys.stderr.buffer.write(b'err\\xe9')"
    )
    result = run_completed_process(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="latin-1",
    )

    assert result.stdout == "café"
    assert result.stderr == "erré"


def test_run_completed_process_text_mode_with_errors() -> None:
    """The errors parameter should control decoder error handling."""
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'bad\\xff'); "
        "sys.stderr.buffer.write(b'fail\\xff')"
    )
    result = run_completed_process(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.stdout == "bad�"
    assert result.stderr == "fail�"


def test_run_completed_process_raises_on_non_zero_return() -> None:
    """check=True should raise :class:`subprocess.CalledProcessError`."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_completed_process(
            [sys.executable, "-c", "import sys; sys.exit(7)"],
            capture_output=True,
            check=True,
        )

    assert excinfo.value.returncode == 7
    assert excinfo.value.output in {b"", ""}
    assert excinfo.value.stderr in {b"", ""}


def test_run_completed_process_enforces_timeouts() -> None:
    """Commands exceeding the timeout should raise :class:`TimeoutExpired`."""
    script = "import time; time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_completed_process(
            [sys.executable, "-c", script],
            timeout=0.1,
        )


def test_run_completed_process_capture_output_conflict() -> None:
    """capture_output may not be combined with explicit stdout/stderr streams."""
    with pytest.raises(
        ValueError, match="stdout and stderr arguments may not be used"
    ):
        run_completed_process(
            [sys.executable, "-c", "print('hi')"],
            capture_output=True,
            stdout=subprocess.PIPE,
        )
