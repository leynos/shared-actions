"""Helpers for importing :mod:`cmd_utils` using ``GITHUB_ACTION_PATH``."""

from __future__ import annotations

import importlib.util
import os
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

MODULE_NAME = "cmd_utils"
MODULE_FILENAME = f"{MODULE_NAME}.py"
ENV_VAR_NAME = "GITHUB_ACTION_PATH"


class CmdUtilsDiscoveryError(RuntimeError):
    """Raised when :mod:`cmd_utils` cannot be located or imported."""

    def __init__(self, message: str, *, searched: list[Path] | None = None) -> None:
        details = message
        if searched:
            joined = " -> ".join(str(path) for path in searched)
            details = f"{message}; searched: {joined}"
        super().__init__(details)
        self.searched = searched or []


def _action_path() -> Path:
    try:
        raw_path = os.environ[ENV_VAR_NAME]
    except KeyError as exc:  # pragma: no cover - defensive guard
        message = f"{ENV_VAR_NAME} is not set in the environment"
        raise CmdUtilsDiscoveryError(message) from exc
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():  # pragma: no cover - defensive guard
        message = f"{ENV_VAR_NAME}={raw_path!r} does not exist"
        raise CmdUtilsDiscoveryError(message)
    return path


def _candidate_module_paths() -> tuple[list[Path], Path]:
    action_path = _action_path()
    searched: list[Path] = []
    for directory in (action_path, *action_path.parents):
        candidate = directory / MODULE_FILENAME
        searched.append(candidate)
        if candidate.is_file() and not candidate.is_symlink():
            return searched, candidate
    message = f"{MODULE_FILENAME} not found via {ENV_VAR_NAME}"
    raise CmdUtilsDiscoveryError(message, searched=searched)


@cache
def import_cmd_utils() -> ModuleType:
    """Import and return the repository-level :mod:`cmd_utils` module."""
    existing = sys.modules.get(MODULE_NAME)
    if isinstance(existing, ModuleType):
        return existing

    searched, module_path = _candidate_module_paths()
    module_dir = str(module_path.parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)

    spec = importlib.util.spec_from_file_location(MODULE_NAME, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - import failure
        message = f"Failed to load spec for {module_path}"
        raise CmdUtilsDiscoveryError(message, searched=searched)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - import-time failure
        message = f"Failed to import cmd_utils from {module_path}"
        raise CmdUtilsDiscoveryError(message, searched=searched) from exc

    sys.modules[MODULE_NAME] = module
    return module


def ensure_cmd_utils_imported() -> None:
    """Ensure :mod:`cmd_utils` is importable and loaded."""
    import_cmd_utils()


__all__ = [
    "CmdUtilsDiscoveryError",
    "ensure_cmd_utils_imported",
    "import_cmd_utils",
]
