"""Helper utilities for validate-linux-packages scripts."""

from __future__ import annotations

import logging
import typing as typ

from plumbum import local
from validate_exceptions import ValidationError

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime fallbacks
    Path = typ.Any
    BaseCommand = typ.Any

__all__ = [
    "ensure_directory",
    "ensure_exists",
    "get_command",
    "unique_match",
]

logger = logging.getLogger(__name__)


def ensure_directory(path: Path, *, exist_ok: bool = True) -> Path:
    """Create ``path`` and return it."""
    path.mkdir(parents=True, exist_ok=exist_ok)
    return path


def ensure_exists(path: Path, message: str) -> None:
    """Raise :class:`ValidationError` when ``path`` does not exist."""
    if not path.exists():
        error = f"{message}: {path}"
        raise ValidationError(error)


def get_command(name: str) -> BaseCommand:
    """Return the ``plumbum`` command named ``name``."""
    try:
        return local[name]
    except Exception as exc:  # pragma: no cover - defensive
        error = f"required command not found: {name}"
        raise ValidationError(error) from exc


def unique_match(paths: typ.Iterable[Path], *, description: str) -> Path:
    """Return the sole match from ``paths`` or raise an error."""
    matches = list(paths)
    if len(matches) != 1:
        error = f"expected exactly one {description}, found {len(matches)}"
        raise ValidationError(error)
    return matches[0]
