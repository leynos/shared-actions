"""Shared helpers for generate-coverage action scripts."""

from __future__ import annotations

import os

import typer

_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSY_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_ALL_BOOL_VALUES: frozenset[str] = _TRUTHY_VALUES | _FALSY_VALUES


def _required_env(name: str) -> str:
    """Return the non-empty value of the required environment variable *name*.

    Raises ``typer.Exit(2)`` when the variable is unset or empty.
    """
    value = os.getenv(name, "").strip()
    if value:
        return value
    typer.echo(f"Missing required environment variable: {name}", err=True)
    raise typer.Exit(2)


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse the environment variable *name* as a boolean.

    Unset or empty values return *default*.  Recognised truthy values are
    ``1``, ``true``, ``yes``, ``on`` (case-insensitive); recognised falsy
    values are ``0``, ``false``, ``no``, ``off``.  Any other non-empty value
    is treated as a configuration error and raises ``typer.Exit(2)``.
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False

    typer.echo(
        f"Invalid boolean value for environment variable {name!r}: {value!r}. "
        f"Expected one of {sorted(_ALL_BOOL_VALUES)} (case-insensitive).",
        err=True,
    )
    raise typer.Exit(2)
