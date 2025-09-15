#!/usr/bin/env python3
"""Validate the ``workspaces`` input for Swatinem/rust-cache mappings."""

from __future__ import annotations

import os
import sys

_ERROR_PREFIX = "Invalid 'workspaces' mapping"


def _error(message: str) -> int:
    """Print *message* to stderr and return an error code."""
    print(f"{_ERROR_PREFIX}: {message}", file=sys.stderr)
    return 1


def _validate_line(line: str, line_no: int) -> int:
    """Validate a single mapping line and return an exit status."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return 0

    if "->" not in stripped:
        message = (
            f"line {line_no} is missing '->' "
            f"(expected 'workspace -> target'): {stripped!r}"
        )
        return _error(message)

    workspace, target = (part.strip() for part in stripped.split("->", 1))
    if not workspace:
        return _error(f"line {line_no} has an empty workspace path")
    if not target:
        return _error(f"line {line_no} has an empty target directory")

    return 0


def main() -> int:
    """Entry point for validating the ``workspaces`` mapping."""
    raw = os.environ.get("INPUT_WORKSPACES", "")
    if not raw.strip():
        return 0

    for line_no, line in enumerate(raw.splitlines(), start=1):
        status = _validate_line(line, line_no)
        if status:
            return status

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
