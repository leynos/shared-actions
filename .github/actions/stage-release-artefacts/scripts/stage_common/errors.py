"""Error types shared across the staging helper package."""

from __future__ import annotations


class StageError(RuntimeError):
    """Raised when the staging pipeline cannot continue."""
