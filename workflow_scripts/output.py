"""Shared output utilities for workflow scripts.

This module provides structured logging and error handling functions
used across the dependabot automerge workflow scripts.
"""

from __future__ import annotations

import json
import sys
import typing as typ


def _log_value(value: object) -> str:
    """Format a value for key=value log output."""
    if value is None:
        return ""
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def emit(key: str, value: object, *, stream: typ.TextIO | None = None) -> None:
    """Print a key=value pair to stdout or the specified stream."""
    target = stream if stream is not None else sys.stdout
    print(f"{key}={_log_value(value)}", file=target)


def fail(message: str) -> typ.NoReturn:
    """Log an error and exit with status code 1."""
    # The literal rather than ``DecisionStatus.ERROR``: this module sits
    # below the decision module, which reaches back here through
    # ``dependabot_merge_state``, so importing the enum would close a
    # cycle. ``test_dependabot_report_snapshots`` holds the two equal.
    emit("automerge_status", "error", stream=sys.stderr)
    emit("automerge_error", message, stream=sys.stderr)
    raise SystemExit(1)
