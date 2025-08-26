"""Utilities for locating and importing :mod:`cmd_utils`."""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final  # noqa: ICN003

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


CMD_UTILS_FILENAME: Final[str] = "cmd_utils.py"
ERROR_REPO_ROOT_NOT_FOUND: Final[str] = "Repository root not found"
ERROR_IMPORT_FAILED: Final[str] = "Failed to import cmd_utils from repository root"


class RepoRootNotFoundError(RuntimeError):
    """Repository root not found."""

    def __init__(self, searched: str) -> None:
        super().__init__(f"{ERROR_REPO_ROOT_NOT_FOUND}; searched: {searched}")


class CmdUtilsImportError(RuntimeError):
    """Failed to import cmd_utils from repository root."""

    def __init__(
        self,
        path: Path,
        symbol: str | None = None,
        *,
        original_exception: Exception | None = None,
    ) -> None:
        detail = f"'{symbol}' not found in {path}" if symbol is not None else str(path)
        self.original_exception = original_exception
        super().__init__(f"{ERROR_IMPORT_FAILED}: {detail}")


def find_repo_root() -> Path:
    """Locate the repository root containing ``CMD_UTILS_FILENAME``."""
    parents = list(Path(__file__).resolve().parents)
    for parent in parents:
        candidate = parent / CMD_UTILS_FILENAME
        if candidate.is_file() and not candidate.is_symlink():
            return parent
    searched = " -> ".join(str(parent / CMD_UTILS_FILENAME) for parent in parents)
    raise RepoRootNotFoundError(searched)


def load_cmd_utils() -> ModuleType:
    """Import and return the ``cmd_utils`` module."""
    repo_root = find_repo_root()
    module_path = repo_root / CMD_UTILS_FILENAME
    spec = importlib.util.spec_from_file_location("cmd_utils", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - import-time failure
        raise CmdUtilsImportError(module_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - import-time failure
        raise CmdUtilsImportError(module_path, original_exception=exc) from exc
    return module


@cache
def _get_cmd_utils() -> ModuleType:
    return load_cmd_utils()


def run_cmd(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - passthrough
    """Proxy ``cmd_utils.run_cmd`` with lazy module loading."""
    try:
        return _get_cmd_utils().run_cmd(*args, **kwargs)
    except AttributeError as exc:  # pragma: no cover - import-time failure
        missing = find_repo_root() / CMD_UTILS_FILENAME
        raise CmdUtilsImportError(missing, "run_cmd", original_exception=exc) from exc


__all__ = [
    "CMD_UTILS_FILENAME",
    "CmdUtilsImportError",
    "RepoRootNotFoundError",
    "find_repo_root",
    "load_cmd_utils",
    "run_cmd",
]
