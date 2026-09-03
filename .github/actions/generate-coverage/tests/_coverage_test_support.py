"""Helpers shared by the coverage-script test modules.

``test_scripts.py`` and ``test_install_cargo_nextest.py`` both load one of the
action's scripts from disk under a fresh module identity and inspect Typer
exits, so those two helpers live here rather than being duplicated across the
two modules. Each module still owns its own ``pytest.fixture`` that wraps
``_load_module`` for the specific script it tests, since neither fixture is
used by the other module.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType

    import pytest


def _exit_code(exc: BaseException) -> int | None:
    """Extract an exit code from Typer or SystemExit exceptions."""
    exit_code = getattr(exc, "exit_code", None)
    if exit_code is None:
        exit_code = getattr(exc, "code", None)
    return exit_code


def _load_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> ModuleType:
    """Import ``name`` from the ``scripts`` directory with real dependencies."""
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    root_dir = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(script_dir)
    monkeypatch.syspath_prepend(root_dir)
    for module_name in (name, "coverage_parsers"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    import importlib as _importlib  # ensure fresh module state for reloads

    _importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, script_dir / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - import failure.
        message = f"could not load {name} from {script_dir}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
