"""Shared helpers for stripping ANSI escape sequences in test assertions."""

from __future__ import annotations

import re

# Match CSI sequences with semicolon- and colon-separated parameters. The
# colon form (e.g. ``\x1b[38:2::255:0:0m``) appears in newer terminals and
# tools.
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")


def strip_ansi(value: str) -> str:
    """Return *value* with ANSI escape sequences removed."""
    return ANSI_ESCAPE_RE.sub("", value)
