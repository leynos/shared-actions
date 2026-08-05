"""Import helpers for the standalone coverage scripts under test.

The action's scripts are executed as ``uv`` script entry points rather than as
an installed package, so tests load them from source with a fresh module state
for each test.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

    import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
ROOT_DIR = Path(__file__).resolve().parents[4]


def load_script_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> ModuleType:
    """Import ``name`` from the ``scripts`` directory with real dependencies."""
    monkeypatch.syspath_prepend(SCRIPT_DIR)
    monkeypatch.syspath_prepend(ROOT_DIR)
    for module_name in (name, "coverage_parsers"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        message = f"unable to load script module {name!r} from {SCRIPT_DIR}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
