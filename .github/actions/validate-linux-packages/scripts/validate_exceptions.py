"""Custom exceptions used by the validate-linux-packages scripts."""

from __future__ import annotations

__all__ = ["ValidationError"]


class ValidationError(RuntimeError):
    """Raised when package validation fails."""

    pass
