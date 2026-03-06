"""Correlation ID helpers."""

from __future__ import annotations

import uuid_utils


def default_uuid7_generator() -> str:
    """Return an RFC 4122 UUIDv7 hex string."""
    return uuid_utils.uuid7().hex
