"""Shared helpers for rust-build-release scripts."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import typer
from plumbum import local

if typ.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand

PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = PKG_DIR.parent.parent.parent.parent

try:  # pragma: no cover - exercised during script execution
    from .cmd_utils import run_cmd
except ImportError:  # pragma: no cover - fallback when run as a script
    module_path = _REPO_ROOT / "cmd_utils.py"
    spec = importlib.util.spec_from_file_location("cmd_utils", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(name="cmd_utils") from None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    run_cmd = module.run_cmd  # type: ignore[assignment]

__all__ = [
    "PKG_DIR",
    "ensure_directory",
    "ensure_exists",
    "get_command",
    "run_cmd",
    "unique_match",
]

PathIterable = typ.Iterable[Path]


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


def unique_match(paths: PathIterable, *, description: str) -> Path:
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
