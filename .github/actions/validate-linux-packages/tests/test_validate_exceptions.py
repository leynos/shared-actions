"""Tests for custom validation exceptions."""

from __future__ import annotations

from scripts.validate_exceptions import ValidationError


def test_validation_error_inherits_runtime_error() -> None:
    """ValidationError should behave like a RuntimeError subclass."""
    err = ValidationError("failure")

    assert isinstance(err, RuntimeError)
    assert str(err) == "failure"
