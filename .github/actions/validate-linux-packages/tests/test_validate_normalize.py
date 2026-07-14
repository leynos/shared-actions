"""Tests for the validate_normalize helper module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from syspath_hack import add_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_normalize.py"
add_to_syspath(SCRIPTS_DIR)


@pytest.fixture(scope="module")
def validate_normalize_module() -> object:
    """Load the validate_normalize module under test."""
    module = sys.modules.get("validate_normalize")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_normalize", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_normalize module"
        raise RuntimeError(message)

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_paths_accepts_canonical(validate_normalize_module: object) -> None:
    """Canonical absolute paths are preserved during normalization."""
    result = validate_normalize_module.normalize_paths(["/usr/bin/tool"])

    assert result == ["/usr/bin/tool"]


def test_normalize_paths_deduplicates(validate_normalize_module: object) -> None:
    """Duplicate paths are removed while preserving the original order."""
    result = validate_normalize_module.normalize_paths(
        ["/usr/bin/tool", "/usr/bin/tool", "/usr/bin/other"]
    )

    assert result == ["/usr/bin/tool", "/usr/bin/other"]


def test_normalize_paths_all_identical(validate_normalize_module: object) -> None:
    """Identical entries collapse to a single canonical path."""
    result = validate_normalize_module.normalize_paths(
        ["/opt/app/tool", "/opt/app/tool", "/opt/app/tool"]
    )

    assert result == ["/opt/app/tool"]


def test_normalize_paths_multiple_duplicate_groups(
    validate_normalize_module: object,
) -> None:
    """Deduplication preserves order when distinct paths repeat."""
    result = validate_normalize_module.normalize_paths(["/a", "/b", "/a", "/b", "/c"])

    assert result == ["/a", "/b", "/c"]


def test_normalize_paths_accepts_empty_list(validate_normalize_module: object) -> None:
    """An empty path list normalizes to an empty result."""
    result = validate_normalize_module.normalize_paths([])

    assert result == []


@pytest.mark.parametrize(
    "path",
    ["//foo", "/usr/../bin"],
)
def test_normalize_paths_rejects_non_canonical(
    validate_normalize_module: object,
    path: str,
) -> None:
    """Non-canonical absolute paths raise ValidationError."""
    with pytest.raises(validate_normalize_module.ValidationError):
        validate_normalize_module.normalize_paths([path])
