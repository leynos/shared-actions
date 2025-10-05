"""Common test utilities for coverage scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from test_support.cmd_mox_stub_adapter import StubManager


def _find_root(start: Path) -> Path:
    """Return the nearest ancestor containing ``pyproject.toml`` or ``.git``."""
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError(str(start))


ROOT = _find_root(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))


@pytest.fixture
def shell_stubs(monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    mgr = StubManager()
    monkeypatch.setenv(
        "PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}"
    )
    try:
        yield mgr
    finally:
        mgr.close()
