"""Tests for toolchain helper utilities."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest


def test_read_default_toolchain_uses_config(
    toolchain_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """read_default_toolchain reads the configured TOOLCHAIN_VERSION file."""
    custom_file = tmp_path / "TOOLCHAIN_VERSION"
    custom_file.write_text("2.0.0\n", encoding="utf-8")
    monkeypatch.setattr(toolchain_module, "TOOLCHAIN_VERSION_FILE", custom_file)
    assert toolchain_module.read_default_toolchain() == "2.0.0"


def test_toolchain_triple_parses_valid_triple(toolchain_module: ModuleType) -> None:
    """toolchain_triple returns the embedded target triple when present."""
    triple = toolchain_module.toolchain_triple("1.89.0-x86_64-unknown-linux-gnu")
    assert triple == "x86_64-unknown-linux-gnu"


def test_toolchain_triple_returns_none_for_short_spec(
    toolchain_module: ModuleType,
) -> None:
    """toolchain_triple returns None when no triple is embedded."""
    assert toolchain_module.toolchain_triple("stable") is None
    assert toolchain_module.toolchain_triple("1.89.0-x86_64") is None
