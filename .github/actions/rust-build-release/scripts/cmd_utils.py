"""Bridge to the repository-level :mod:`cmd_utils` utilities."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

__all__ = ["run_cmd"]

_REPO_CMD_UTILS_NAME = "cmd_utils"
_REPO_CMD_UTILS_PATH = Path(__file__).resolve().parents[4] / "cmd_utils.py"


def _load_cmd_utils_module() -> ModuleType:
    module = sys.modules.get(_REPO_CMD_UTILS_NAME)
    if module and hasattr(module, "run_cmd"):
        return module
    spec = importlib.util.spec_from_file_location(
        "shared_actions.cmd_utils", _REPO_CMD_UTILS_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Cannot load cmd_utils from {_REPO_CMD_UTILS_PATH}")
    module_obj = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module_obj)
    spec.loader.exec_module(module_obj)
    sys.modules.setdefault(_REPO_CMD_UTILS_NAME, module_obj)
    return module_obj


def _getattr(module: ModuleType, attr: str) -> Callable[..., Any]:
    if not hasattr(module, attr):  # pragma: no cover - defensive
        raise AttributeError(f"Module {module.__name__} missing attribute {attr}")
    return getattr(module, attr)


run_cmd = _getattr(_load_cmd_utils_module(), "run_cmd")
