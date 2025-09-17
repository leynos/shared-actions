"""Bridge to the repository-level :mod:`cmd_utils` utilities."""

from __future__ import annotations

import importlib.util
import sys
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:
    import collections.abc as cabc
    import types

__all__ = ["run_cmd"]

_REPO_CMD_UTILS_NAME = "cmd_utils"
_REPO_CMD_UTILS_PATH = Path(__file__).resolve().parents[4] / "cmd_utils.py"


class CmdUtilsLoadError(ImportError):
    """Raised when the repository-level cmd_utils module cannot be loaded."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Cannot load cmd_utils from {path}")


class CmdUtilsAttributeError(AttributeError):
    """Raised when the shimmed cmd_utils module misses an expected attribute."""

    def __init__(self, module_name: str, attribute: str) -> None:
        super().__init__(f"Module {module_name} missing attribute {attribute}")


def _load_cmd_utils_module() -> types.ModuleType:
    module = sys.modules.get(_REPO_CMD_UTILS_NAME)
    if module and hasattr(module, "run_cmd"):
        return module
    spec = importlib.util.spec_from_file_location(
        "shared_actions.cmd_utils", _REPO_CMD_UTILS_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise CmdUtilsLoadError(_REPO_CMD_UTILS_PATH)
    module_obj = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module_obj)
    spec.loader.exec_module(module_obj)
    sys.modules.setdefault(_REPO_CMD_UTILS_NAME, module_obj)
    return module_obj


def _getattr(module: types.ModuleType, attr: str) -> cabc.Callable[..., t.Any]:
    if not hasattr(module, attr):  # pragma: no cover - defensive
        raise CmdUtilsAttributeError(module.__name__, attr)
    return getattr(module, attr)


run_cmd = _getattr(_load_cmd_utils_module(), "run_cmd")
