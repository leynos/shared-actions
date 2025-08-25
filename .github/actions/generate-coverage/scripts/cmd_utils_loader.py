"""Utilities for locating and importing :mod:`cmd_utils`."""

from __future__ import annotations

import importlib.util
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - type hints only
    import collections.abc as cabc
    from types import ModuleType

CMD_UTILS_FILENAME: t.Final[str] = "cmd_utils.py"
ERROR_REPO_ROOT_NOT_FOUND: t.Final[str] = "Repository root not found"
ERROR_IMPORT_FAILED: t.Final[str] = "Failed to import cmd_utils from repository root"


class RepoRootNotFoundError(RuntimeError):
    """Repository root not found."""

    def __init__(self, searched: str) -> None:
        super().__init__(f"{ERROR_REPO_ROOT_NOT_FOUND}; searched: {searched}")


class CmdUtilsImportError(RuntimeError):
    """Failed to import cmd_utils from repository root."""

    def __init__(self, path: Path, symbol: str | None = None) -> None:
        detail = f"'{symbol}' not found in {path}" if symbol is not None else str(path)
        super().__init__(f"{ERROR_IMPORT_FAILED}: {detail}")


def find_repo_root() -> Path:
    """Locate the repository root containing ``CMD_UTILS_FILENAME``."""
    parents = list(Path(__file__).resolve().parents)
    for parent in parents:
        if (parent / CMD_UTILS_FILENAME).exists():
            return parent
    searched = " -> ".join(str(p) for p in parents)
    raise RepoRootNotFoundError(searched)


def load_cmd_utils() -> ModuleType:
    """Import and return the ``cmd_utils`` module."""
    repo_root = find_repo_root()
    spec = importlib.util.spec_from_file_location(
        "cmd_utils", repo_root / CMD_UTILS_FILENAME
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import-time failure
        raise CmdUtilsImportError(repo_root)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cmd_utils = load_cmd_utils()
try:
    run_cmd: cabc.Callable[..., t.Any] = cmd_utils.run_cmd
except AttributeError as exc:  # pragma: no cover - import-time failure
    missing = find_repo_root() / CMD_UTILS_FILENAME
    raise CmdUtilsImportError(missing, "run_cmd") from exc


__all__ = [
    "CmdUtilsImportError",
    "RepoRootNotFoundError",
    "find_repo_root",
    "load_cmd_utils",
    "run_cmd",
]
