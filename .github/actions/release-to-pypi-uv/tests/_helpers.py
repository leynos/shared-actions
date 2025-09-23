"""Test helpers for release-to-pypi-uv action scripts."""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

_ACTION_PATH = os.environ.get("GITHUB_ACTION_PATH")

if _ACTION_PATH:
    _action_root = Path(_ACTION_PATH).resolve()
    SCRIPTS_DIR = _action_root / "scripts"
    REPO_ROOT = _action_root.parents[2]
else:
    SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
    REPO_ROOT = SCRIPTS_DIR.parents[3]


def load_script_module(name: str) -> ModuleType:
    """Load a script module by *name* from the action's scripts directory."""
    script_path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"release_to_pypi_uv_{name}", script_path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import failure
        message = f"Unable to load script module {name} from {script_path}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    # Register module in sys.modules so importlib.reload works in tests
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


__all__ = ["REPO_ROOT", "SCRIPTS_DIR", "load_script_module"]
