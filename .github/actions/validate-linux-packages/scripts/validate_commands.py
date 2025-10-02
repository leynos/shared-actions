"""Command execution helpers for the validate-linux-packages scripts."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime fallback
    BaseCommand = typ.Any

__all__ = ["run_text"]


def _decode(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_text(command: BaseCommand, *, timeout: int | None = None) -> str:
    """Execute ``command`` and return its stdout as ``str``."""
    _, stdout, _ = command.run(timeout=timeout)
    return _decode(stdout)
