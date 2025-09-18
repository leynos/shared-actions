"""Test helpers for release-to-pypi-uv action scripts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[3]


def load_script_module(name: str) -> Any:
    """Load a script module by *name* from the action's scripts directory."""
    script_path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"release_to_pypi_uv_{name}", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover - import failure
        raise RuntimeError(f"Unable to load script module {name} from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


__all__ = ["load_script_module", "REPO_ROOT", "SCRIPTS_DIR"]
