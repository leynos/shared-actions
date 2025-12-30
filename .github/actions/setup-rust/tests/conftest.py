"""Common test utilities for setup-rust scripts."""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest
from syspath_hack import find_project_root, prepend_to_syspath

if sys.platform.startswith("win"):
    pytest.skip("cmd-mox IPC is unavailable on Windows", allow_module_level=True)

from test_support.cmd_mox_stub_adapter import StubManager

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


ROOT = find_project_root(start=Path(__file__).resolve().parent)
prepend_to_syspath(ROOT)


@pytest.fixture
def shell_stubs(cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    monkeypatch.setenv("PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}")
    with StubManager(cmd_mox) as mgr:
        yield mgr
