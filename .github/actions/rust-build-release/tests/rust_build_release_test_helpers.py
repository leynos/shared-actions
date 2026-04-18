"""Shared helpers for rust-build-release tests."""

from __future__ import annotations


def assert_no_toolchain_override(parts: list[str]) -> None:
    """Assert that a cross command does not inject a +toolchain override."""
    assert parts[1] == "build"  # noqa: S101
    assert all(not part.startswith("+") for part in parts[1:])  # noqa: S101
