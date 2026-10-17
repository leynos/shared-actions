"""Helpers shared by the ci-telemetry-finish tests: load the scripts by path."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType

ACTION_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = ACTION_DIR / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
START_SCRIPT = ACTION_DIR.parent / "ci-telemetry-start" / "scripts" / "sampler.py"


def load(name: str, path: Path) -> ModuleType:
    """Import the script at *path* under *name*; the scripts are no package."""
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        message = f"cannot load {path}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def summarize() -> ModuleType:
    """Return the ``summarize`` module."""
    return load("ct_summarize", SCRIPTS / "summarize.py")


def sccache_stats() -> ModuleType:
    """Return the ``sccache_stats`` module."""
    return load("ct_sccache_stats", SCRIPTS / "sccache_stats.py")


def finish() -> ModuleType:
    """Return the ``finish`` module."""
    return load("ct_finish", SCRIPTS / "finish.py")


def sampler() -> ModuleType:
    """Return the start action's ``sampler`` module."""
    return load("ct_sampler", START_SCRIPT)
