from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _find_root(start: Path) -> Path:
    """Return the nearest ancestor containing ``pyproject.toml`` or ``.git``."""

    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError(f"Project root not found from {start}")


ROOT = _find_root(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))

from shellstub import StubManager


@pytest.fixture
def shell_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""

    dir_ = tmp_path / "stubs"
    mgr = StubManager(dir_)
    import shellstub as mod
    mod._GLOBAL_MANAGER = mgr
    monkeypatch.setenv("PATH", f"{dir_}{os.pathsep}{os.getenv('PATH')}")
    monkeypatch.setenv(
        "PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH','')}"
    )
    yield mgr

