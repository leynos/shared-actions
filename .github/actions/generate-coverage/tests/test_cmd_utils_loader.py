"""Tests for the :mod:`cmd_utils_loader` helper module."""

from __future__ import annotations

import importlib.util
import sys
import typing as t
from pathlib import Path

import pytest

if t.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


def _load_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cmd_utils_content: str | None = None,
) -> ModuleType:
    """Return a fresh instance of ``cmd_utils_loader`` from ``tmp_path``."""
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "cmd_utils_loader.py"
    ).read_text()
    loader_path = tmp_path / "cmd_utils_loader.py"
    loader_path.write_text(source)
    if cmd_utils_content is not None:
        (tmp_path / "cmd_utils.py").write_text(cmd_utils_content)
    monkeypatch.syspath_prepend(tmp_path)
    monkeypatch.delitem(sys.modules, "cmd_utils_loader", raising=False)
    spec = importlib.util.spec_from_file_location("cmd_utils_loader", loader_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_repo_root_reports_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``find_repo_root`` lists all candidate paths in its error."""
    mod = _load_loader(tmp_path, monkeypatch)
    with pytest.raises(mod.RepoRootNotFoundError) as excinfo:
        mod.find_repo_root()
    msg = str(excinfo.value)
    loader_file = tmp_path / "cmd_utils_loader.py"
    expected = [
        (parent / mod.CMD_UTILS_FILENAME).resolve()
        for parent in loader_file.resolve().parents
    ]
    for candidate in expected:
        assert str(candidate) in msg


def test_run_cmd_missing_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_cmd`` raises a clear error when ``cmd_utils.run_cmd`` is missing."""
    mod = _load_loader(tmp_path, monkeypatch, cmd_utils_content="")
    with pytest.raises(mod.CmdUtilsImportError) as excinfo:
        mod.run_cmd("echo")
    msg = str(excinfo.value)
    assert "run_cmd" in msg
    assert str(tmp_path / mod.CMD_UTILS_FILENAME) in msg
