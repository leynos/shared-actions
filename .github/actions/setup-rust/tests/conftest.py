"""Common test utilities for setup-rust scripts."""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest

if sys.platform.startswith("win"):
    pytest.skip("cmd-mox IPC is unavailable on Windows", allow_module_level=True)

from test_support.cmd_mox_stub_adapter import StubManager

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


def _find_root(start: Path) -> Path:
    """Return nearest ancestor containing ``pyproject.toml`` or ``.git``."""
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError(str(start))


ROOT = _find_root(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))


@pytest.fixture
def shell_stubs(cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    monkeypatch.setenv("PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}")
    with StubManager(cmd_mox) as mgr:
        yield mgr
