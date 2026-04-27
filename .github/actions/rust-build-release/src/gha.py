"""GitHub Actions workflow command helpers."""

from __future__ import annotations

import typing as typ

import typer


class _Echo(typ.Protocol):
    """Callable shape used by typer.echo-compatible adapters."""

    def __call__(self, message: str, *, err: bool = False) -> None:
        """Emit *message*, optionally to stderr."""
        ...


def debug(message: str, *, echo: _Echo = typer.echo) -> None:
    """Emit a ::debug:: workflow command."""
    echo(f"::debug:: {message}")


def warning(message: str, *, echo: _Echo = typer.echo) -> None:
    """Emit a ::warning:: workflow command."""
    echo(f"::warning:: {message}", err=True)


def error(message: str, *, echo: _Echo = typer.echo) -> None:
    """Emit a ::error:: workflow command."""
    echo(f"::error:: {message}", err=True)
