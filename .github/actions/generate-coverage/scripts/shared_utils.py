"""Helpers for reading stored coverage baselines."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from pathlib import Path

__all__ = ["read_previous_coverage"]


def read_previous_coverage(baseline: Path | None) -> str | None:
    """Return the stored coverage percent if the file exists and is valid."""
    if baseline and baseline.is_file():
        try:
            return f"{float(baseline.read_text().strip()):.2f}"
        except (ValueError, OSError):
            return None
    return None
