"""Utilities for locating and importing :mod:`cmd_utils`."""

from __future__ import annotations

import importlib.util
import os
import typing as typ
from functools import cache
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


CMD_UTILS_FILENAME: typ.Final[str] = "cmd_utils.py"
ERROR_REPO_ROOT_NOT_FOUND: typ.Final[str] = "Repository root not found"
ERROR_IMPORT_FAILED: typ.Final[str] = "Failed to import cmd_utils from repository root"


class RepoRootNotFoundError(RuntimeError):
    """Repository root not found."""

    def __init__(self, searched: str) -> None:
        self.searched = searched
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
        cause = (
            f" (cause: {type(original_exception).__name__}: {original_exception})"
            if original_exception
            else ""
        )
        self.original_exception = original_exception
        super().__init__(f"{ERROR_IMPORT_FAILED}: {detail}{cause}")


def find_repo_root() -> Path:
    """Locate the repository root containing ``CMD_UTILS_FILENAME``."""
    candidates: list[Path] = []
    env_path = os.environ.get("GITHUB_ACTION_PATH")
    search_roots: list[Path]
    if env_path:
        action_path = Path(env_path).expanduser().resolve()
        search_roots = [action_path, *action_path.parents]
    else:  # pragma: no cover - fallback for local tooling
        search_roots = list(Path(__file__).resolve().parents)
    for parent in search_roots:
        candidate = parent / CMD_UTILS_FILENAME
        candidates.append(candidate)
        if candidate.is_file() and not candidate.is_symlink():
            return parent
    searched = " -> ".join(str(path) for path in candidates) + " (symlinks ignored)"
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


def run_cmd(*args: typ.Any, **kwargs: typ.Any) -> typ.Any:  # noqa: ANN401 - passthrough
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
