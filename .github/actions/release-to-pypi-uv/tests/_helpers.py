"""Test helpers for release-to-pypi-uv action scripts."""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

# The action scripts ship alongside the tests in this repository, so the
# local path is authoritative. ``GITHUB_ACTION_PATH`` is only a fallback
# for relocated layouts: the Makefile exports it at the repository root,
# whose own ``scripts/`` directory belongs to a different action and must
# not be used to locate these scripts.
_scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
action_path = os.environ.get("GITHUB_ACTION_PATH")
if not _scripts_dir.is_dir() and action_path:
    scripts_candidate = Path(action_path).resolve() / "scripts"
    if scripts_candidate.is_dir():
        _scripts_dir = scripts_candidate

SCRIPTS_DIR = _scripts_dir
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
