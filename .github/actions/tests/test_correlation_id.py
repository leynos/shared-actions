"""Tests for :mod:`correlation_id`."""

from __future__ import annotations

import time

from correlation_id import default_uuid7_generator

_HEX_DIGITS = set("0123456789abcdef")


def _assert_lowercase_hex(value: str) -> None:
    """Assert the value is a lowercase hex string of length 32."""
    assert len(value) == 32
    assert value == value.lower()
    assert set(value) <= _HEX_DIGITS


def _extract_timestamp_ms(value: str) -> int:
    """Extract the millisecond timestamp from a UUIDv7 hex string."""
    return int(value[:12], 16)


class TestDefaultUuid7Generator:
    """Tests for the default UUIDv7 generator."""

    def test_returns_lowercase_hex(self) -> None:
        """Generator returns lowercase hex output."""
        value = default_uuid7_generator()
        _assert_lowercase_hex(value)

    def test_sets_version_and_variant(self) -> None:
        """Generator sets RFC 4122 version and variant bits."""
        value = default_uuid7_generator()
        assert value[12] == "7"
        assert value[16] in {"8", "9", "a", "b"}

    def test_timestamp_within_call_window(self) -> None:
        """Generator timestamps fall within the call window."""
        start_ms = time.time_ns() // 1_000_000
        value = default_uuid7_generator()
        end_ms = time.time_ns() // 1_000_000

        timestamp_ms = _extract_timestamp_ms(value)
        assert start_ms <= timestamp_ms <= end_ms

    def test_generates_unique_values(self) -> None:
        """Generator produces unique values across calls."""
        values = {default_uuid7_generator() for _ in range(1_000)}
        assert len(values) == 1_000
