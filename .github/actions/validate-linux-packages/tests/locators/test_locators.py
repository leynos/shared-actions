"""Tests covering package locator utilities."""

from __future__ import annotations

import typing as typ

import pytest

from tests.helpers import write_package

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
    from types import ModuleType


def test_ensure_subset_reports_missing_entries(
    validate_packages_module: ModuleType,
) -> None:
    """ensure_subset raises a ValidationError when paths are missing."""
    with pytest.raises(
        validate_packages_module.ValidationError,
        match="missing payload",
    ):
        validate_packages_module.ensure_subset(
            ("/usr/bin/tool",),
            (),
            "payload",
        )


def test_locate_deb_returns_unique_package(
    validate_packages_module: ModuleType, tmp_path: Path
) -> None:
    """locate_deb finds the matching artefact in the directory."""
    package = write_package(tmp_path, "tool_1.2.3-1_amd64.deb", content=b"")

    result = validate_packages_module.locate_deb(tmp_path, "tool", "1.2.3", "1")

    assert result == package


def test_locate_deb_rejects_ambiguous_matches(
    validate_packages_module: ModuleType,
    tmp_path: Path,
) -> None:
    """locate_deb raises when multiple candidates are found."""
    write_package(tmp_path, "tool_1.2.3-1_amd64.deb", content=b"")
    write_package(tmp_path, "tool_1.2.3-1_arm64.deb", content=b"")

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="expected exactly one tool deb package",
    ):
        validate_packages_module.locate_deb(tmp_path, "tool", "1.2.3", "1")


def test_locate_rpm_rejects_ambiguous_matches(
    validate_packages_module: ModuleType,
    tmp_path: Path,
) -> None:
    """locate_rpm raises when multiple candidates are found."""
    write_package(tmp_path, "tool-1.2.3-1.aarch64.rpm", content=b"")
    write_package(tmp_path, "tool-1.2.3-1.x86_64.rpm", content=b"")

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="expected exactly one tool rpm package",
    ):
        validate_packages_module.locate_rpm(tmp_path, "tool", "1.2.3", "1")
