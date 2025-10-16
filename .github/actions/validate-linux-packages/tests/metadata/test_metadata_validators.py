"""Tests for metadata validator helpers."""

from __future__ import annotations

import re
import typing as typ

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from types import ModuleType


def test_metadata_validator_raise_error_message(
    validate_packages_module: ModuleType,
) -> None:
    """raise_error reports the expected and actual values."""
    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected field: expected 'foo'",
    ):
        validate_packages_module._MetadataValidators.raise_error(
            "unexpected field", "foo", "bar"
        )


def test_metadata_validator_equal_accepts_match(
    validate_packages_module: ModuleType,
) -> None:
    """Equal validator returns successfully when the attribute matches."""

    class Meta:
        name = "package"

    validator = validate_packages_module._MetadataValidators.equal(
        "name", "package", "unexpected package name"
    )

    validator(Meta())


def test_metadata_validator_equal_rejects_mismatch(
    validate_packages_module: ModuleType,
) -> None:
    """Equal validator raises when the attribute differs."""

    class Meta:
        name = "other"

    validator = validate_packages_module._MetadataValidators.equal(
        "name", "package", "unexpected package name"
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected package name",
    ):
        validator(Meta())


def test_metadata_validator_in_set_uses_formatter(
    validate_packages_module: ModuleType,
) -> None:
    """In_set validator applies the provided formatter in error messages."""

    class Meta:
        version = "2.0.0"

    validator = validate_packages_module._MetadataValidators.in_set(
        "version",
        {"1.0.0", "1.1.0"},
        "unexpected version",
        fmt_expected=lambda values: " or ".join(sorted(values)),
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match=re.escape("1.0.0 or 1.1.0"),
    ):
        validator(Meta())


def test_metadata_validator_prefix_accepts_blank(
    validate_packages_module: ModuleType,
) -> None:
    """Prefix validator permits blank values."""

    class Meta:
        release = ""

    validator = validate_packages_module._MetadataValidators.prefix(
        "release", "1", "unexpected release"
    )

    validator(Meta())


def test_metadata_validator_prefix_rejects_non_matching(
    validate_packages_module: ModuleType,
) -> None:
    """Prefix validator raises when the attribute lacks the prefix."""

    class Meta:
        release = "2.el9"

    validator = validate_packages_module._MetadataValidators.prefix(
        "release", "1", "unexpected release"
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="starting with '1'",
    ):
        validator(Meta())
