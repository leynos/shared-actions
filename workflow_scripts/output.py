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
    emit("automerge_status", "error", stream=sys.stderr)
    emit("automerge_error", message, stream=sys.stderr)
    raise SystemExit(1)
