"""Common test utilities for coverage scripts."""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest
from syspath_hack import find_project_root, prepend_to_syspath

if sys.platform.startswith("win"):
    pytest.skip("cmd-mox IPC is unavailable on Windows", allow_module_level=True)

from _coverage_test_support import _load_module

from test_support.cmd_mox_stub_adapter import StubManager

if typ.TYPE_CHECKING:
    from types import ModuleType

    from cmd_mox import CmdMox


ROOT = find_project_root(start=Path(__file__).resolve().parent)
prepend_to_syspath(ROOT)


@pytest.fixture
def shell_stubs(cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    with StubManager(cmd_mox) as mgr:
        monkeypatch.setenv(
            "PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}"
        )
        yield mgr


@pytest.fixture
def install_nextest_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``install_cargo_nextest`` module for testing.

    Defined here, rather than in ``_coverage_test_support.py``, because both
    ``test_install_cargo_nextest.py`` and ``test_install_cargo_nextest_install.py``
    need it: a conftest fixture is visible to every test module in this
    directory without an explicit import, avoiding the false "redefinition"
    ruff otherwise reports for a same-named import shadowed by same-named
    fixture parameters across many tests.

    Clears ``GITHUB_STEP_SUMMARY`` and ``GITHUB_PATH`` so tests that do not
    explicitly point them at a ``tmp_path`` file cannot leak bounded metric
    lines or PATH exports into the real job when this suite itself runs
    inside a GitHub Actions job.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    return _load_module(monkeypatch, "install_cargo_nextest")
