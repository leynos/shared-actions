"""Black-box test for a standard error whose reader has gone.

Under ``make test 2>&1 | head`` nobody reads the helper's standard error once
``head`` exits. The helper must drop its own summary lines quietly and still
exit with uv's status.
"""

from __future__ import annotations

import os

from _harness import OFFLINE_SYNC, Harness, fixture_text, response, rule


def test_a_closed_standard_error_never_changes_uv_s_status(harness: Harness) -> None:
    """The summary lines are dropped, not raised, when nobody reads stderr.

    uv's own status must still be the helper's status, not a traceback or the
    interpreter's 120 from a failed flush at exit.
    """
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        result = harness.run(
            ["prepare"],
            [rule(OFFLINE_SYNC, response(1, fixture_text("missing_lock")))],
            stderr=write_end,
        )
    finally:
        os.close(write_end)
    assert result.status == 1
    assert result.stdout == ""
