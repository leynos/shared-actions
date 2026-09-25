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
from _llvm_cov_test_support import INSTALLER_COPIES, _load_installer

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


@pytest.fixture(params=list(INSTALLER_COPIES), ids=list(INSTALLER_COPIES))
def install_llvm_cov_module(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Return a freshly loaded installer copy with job-level side effects disabled.

    Declared here rather than in one test module because the installer's tests
    are split across three modules by responsibility and each needs it; a
    conftest fixture is visible to all of them without an import that reads as
    unused. Both actions ship the installer, so the fixture is parametrised
    over the two copies and the ratchet-coverage one is executed rather than
    assumed identical.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.delenv("RUNNER_OS", raising=False)
    monkeypatch.delenv("RUNNER_ARCH", raising=False)
    if request.param == "generate-coverage":
        return _load_module(monkeypatch, "install_cargo_llvm_cov")
    return _load_installer(
        INSTALLER_COPIES[request.param], f"install_cargo_llvm_cov_{request.param}"
    )
