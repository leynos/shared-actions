"""Shared helpers for rust-build-release scripts."""

from __future__ import annotations

import sys
import typing as t
from pathlib import Path

import typer
from plumbum import local

try:  # pragma: no cover - exercised during script execution
    from .cmd_utils import run_cmd
except ImportError:  # pragma: no cover - fallback when run as a script
    from importlib import util
    from types import ModuleType

    _PKG_DIR = Path(__file__).resolve().parent
    _PKG_NAME = "rust_build_release_scripts"
    if _PKG_NAME not in sys.modules:
        pkg = ModuleType(_PKG_NAME)
        pkg.__path__ = [str(_PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules[_PKG_NAME] = pkg
    _SPEC = util.spec_from_file_location(
        f"{_PKG_NAME}.cmd_utils", _PKG_DIR / "cmd_utils.py"
    )
    if _SPEC is None or _SPEC.loader is None:
        msg = "Unable to load cmd_utils helper"
        raise ImportError(msg) from None
    _MODULE = util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
    run_cmd = _MODULE.run_cmd  # type: ignore[assignment]

__all__ = [
    "ensure_directory",
    "ensure_exists",
    "get_command",
    "run_cmd",
    "unique_match",
]


if t.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand


def get_command(name: str) -> BaseCommand:
    """Return a ``plumbum`` command, exiting with an error if it is missing."""
    try:
        return local[name]
    except Exception as exc:  # pragma: no cover - error path
        typer.secho(
            f"Required command not found: {name}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(127) from exc


def ensure_exists(path: Path, message: str) -> None:
    """Exit with an error if ``path`` does not exist."""
    if not path.exists():  # pragma: no cover - defensive check
        typer.secho(f"error: {message}: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)


def ensure_directory(path: Path, *, exist_ok: bool = True) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=exist_ok)
    return path


def unique_match(paths: t.Iterable[Path], *, description: str) -> Path:
    """Return the sole path in ``paths`` or exit with an error."""
    matches = list(paths)
    if len(matches) != 1:
        typer.secho(
            f"error: expected exactly one {description}, found {len(matches)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    return matches[0]
