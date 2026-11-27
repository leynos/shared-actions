"""Black-box tests for a standard error whose reader has gone.

Under ``make test 2>&1 | head`` nobody reads the helper's standard error once
``head`` exits. The helper must keep draining uv's output, drop its own summary
lines quietly and still exit with the status of the work it ran. These tests
need a POSIX process group, so they sit beside the other black-box tests, which
the Windows legs skip.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

from _harness import (
    OFFLINE_SYNC,
    ONLINE_SYNC,
    Harness,
    Result,
    fixture_text,
    offline_then_online,
    response,
    rule,
)

import uv_gate


def _closed_stderr_run(harness: Harness, rules: list[dict[str, object]]) -> Result:
    """Run ``prepare`` with a standard error nobody reads."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        return harness.run(["prepare"], rules, stderr=write_end)
    finally:
        os.close(write_end)


def test_a_failing_uv_keeps_its_status_when_stderr_is_closed(harness: Harness) -> None:
    """The summary lines are dropped, not raised, when nobody reads stderr.

    uv's own status must still be the helper's status, not a traceback or the
    interpreter's 120 from a failed flush at exit.
    """
    result = _closed_stderr_run(
        harness, [rule(OFFLINE_SYNC, response(1, fixture_text("missing_lock")))]
    )
    assert result.status == 1
    assert result.stdout == ""


def test_a_succeeding_uv_with_no_stderr_still_succeeds(harness: Harness) -> None:
    """Even the closing metric line, the only output here, is dropped quietly."""
    result = _closed_stderr_run(harness, [rule(OFFLINE_SYNC, response(0))])
    assert result.status == 0
    assert result.argvs() == [OFFLINE_SYNC]


def test_a_recoverable_miss_still_retries_online_when_stderr_is_closed(
    harness: Harness,
) -> None:
    """The one online attempt happens, and its success is the final status."""
    result = _closed_stderr_run(harness, offline_then_online("cache_miss"))
    assert result.status == 0
    assert result.argvs() == [OFFLINE_SYNC, ONLINE_SYNC]


def test_a_broken_standard_error_does_not_stall_the_child() -> None:
    """When forwarding fails the helper keeps draining, so the child still exits.

    The driver runs with a standard error whose reader has gone. The child
    writes far more than a pipe holds; a helper that stopped reading would
    leave it blocked and the run would time out. The tail kept for
    classification must still reach the end of the output.
    """
    child = "import sys; sys.stderr.write('x' * 300000 + 'END')"
    helper_dir = str(pathlib.Path(uv_gate.__file__).parent)
    driver = (
        "import sys; "
        f"sys.path.insert(0, {helper_dir!r}); "
        "import uv_gate; "
        "status, tail = uv_gate._run_streaming("
        f"[sys.executable, '-c', {child!r}], {{}}); "
        "sys.stdout.write(f'{status} {tail[-3:]}')"
    )
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        process = subprocess.Popen(  # noqa: S603  # fixed argv, test-only driver
            [sys.executable, "-c", driver],
            stderr=write_end,
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            # A regression leaves the driver and its child blocked on the pipe.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    finally:
        os.close(write_end)
    assert process.returncode == 0
    assert stdout == "0 END"
