"""Common test utilities for coverage scripts."""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from shellstub import StubManager


def _find_root(start: Path) -> Path:
    """Return the nearest ancestor containing ``pyproject.toml`` or ``.git``."""
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError(str(start))


ROOT = _find_root(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))


@pytest.fixture
def shell_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    from shellstub import StubManager

    dir_ = tmp_path / "stubs"
    mgr = StubManager(dir_)
    import shellstub as mod

    mod._GLOBAL_MANAGER = mgr
    monkeypatch.setenv("PATH", f"{dir_}{os.pathsep}{os.getenv('PATH')}")
    monkeypatch.setenv("PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}")
    return mgr
