"""Command execution helpers for the validate-linux-packages scripts."""

from __future__ import annotations

import typing as typ

from plumbum.commands.processes import ProcessExecutionError
from validate_exceptions import ValidationError

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


def _format_command(command: BaseCommand | typ.Sequence[object] | None) -> str:
    if command is None:
        return "<unknown command>"
    if argv := getattr(command, "argv", None):
        return " ".join(str(part) for part in argv)
    if isinstance(command, tuple | list):
        return " ".join(str(part) for part in command)
    return repr(command)


def run_text(command: BaseCommand, *, timeout: int | None = None) -> str:
    """Execute ``command`` and return its stdout as ``str``."""
    try:
        _, stdout, _ = command.run(timeout=timeout)
    except ProcessExecutionError as exc:
        formatted = _format_command(getattr(exc, "argv", None) or command)
        message = f"command failed with exit code {exc.retcode}: {formatted}".strip()
        raise ValidationError(message) from exc
    except Exception as exc:  # pragma: no cover - defensive
        message = f"command execution failed: {_format_command(command)} ({exc})"
        raise ValidationError(message) from exc
    return _decode(stdout)
