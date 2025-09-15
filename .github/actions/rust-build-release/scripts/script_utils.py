"""Shared helpers for rust-build-release scripts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import typer
from plumbum import local
from plumbum.commands.base import BaseCommand

# Ensure the repository root is on ``sys.path`` so ``cmd_utils`` can be imported
# when these scripts are executed directly via ``python path/to/script.py``.
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from cmd_utils import run_cmd as _run_cmd  # noqa: E402

__all__ = [
    "REPO_ROOT",
    "ensure_directory",
    "ensure_exists",
    "get_command",
    "run_cmd",
    "unique_match",
]


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


def unique_match(paths: Iterable[Path], *, description: str) -> Path:
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


# Re-export the shared ``run_cmd`` helper so callers can import it from this
# module without having to manipulate ``sys.path`` themselves.
run_cmd = _run_cmd
