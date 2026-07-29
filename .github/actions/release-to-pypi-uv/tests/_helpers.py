"""Test helpers for release-to-pypi-uv action scripts."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

# Resolve the action's scripts directory from this file's location rather than
# from ``GITHUB_ACTION_PATH``. That variable describes whichever action is
# currently executing — the Makefile points it at the repository root and a
# composite action run points it at that action's directory — so trusting it
# here makes the helper load another action's scripts (or none at all).
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
