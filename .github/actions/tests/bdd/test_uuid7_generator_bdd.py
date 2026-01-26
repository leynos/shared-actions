"""BDD scenarios for default UUIDv7 correlation IDs."""

from __future__ import annotations

import time

import pytest
from pytest_bdd import parsers, scenarios, then, when

from correlation_id import default_uuid7_generator

scenarios("features/uuid7_generator.feature")

_HEX_DIGITS = set("0123456789abcdef")


@pytest.fixture
def context() -> dict[str, object]:
    """Shared context for BDD steps."""
    return {}


@when("I generate a default UUIDv7 correlation ID")
def generate_default_uuid7(context: dict[str, object]) -> None:
    """Generate a single UUIDv7 correlation ID and capture timing."""
    start_ms = time.time_ns() // 1_000_000
    value = default_uuid7_generator()
    end_ms = time.time_ns() // 1_000_000

    context["value"] = value
    context["start_ms"] = start_ms
    context["end_ms"] = end_ms


@then("the ID is a lowercase hex string of length 32")
def assert_lowercase_hex(context: dict[str, object]) -> None:
    """Validate the UUIDv7 output format."""
    value = context["value"]
    assert isinstance(value, str)
    assert len(value) == 32
    assert value == value.lower()
    assert set(value) <= _HEX_DIGITS


@then("the ID has RFC 4122 version and variant bits")
def assert_version_and_variant(context: dict[str, object]) -> None:
    """Validate the UUID version and variant nibbles."""
    value = context["value"]
    assert isinstance(value, str)
    assert value[12] == "7"
    assert value[16] in {"8", "9", "a", "b"}


@then("the timestamp is within the request window")
def assert_timestamp_window(context: dict[str, object]) -> None:
    """Validate the embedded timestamp window."""
    value = context["value"]
    start_ms = context["start_ms"]
    end_ms = context["end_ms"]
    assert isinstance(value, str)
    assert isinstance(start_ms, int)
    assert isinstance(end_ms, int)

    timestamp_ms = int(value[:12], 16)
    assert start_ms <= timestamp_ms <= end_ms


@when(parsers.parse("I generate {count:d} correlation IDs"))
def generate_multiple(context: dict[str, object], count: int) -> None:
    """Generate multiple UUIDv7 correlation IDs."""
    context["values"] = [default_uuid7_generator() for _ in range(count)]


@then("all generated IDs are unique")
def assert_unique(context: dict[str, object]) -> None:
    """Validate uniqueness for generated IDs."""
    values = context["values"]
    assert isinstance(values, list)
    assert len(set(values)) == len(values)
