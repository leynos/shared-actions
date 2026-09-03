"""Shared helpers for generate-coverage action scripts."""

from __future__ import annotations

import os
import typing as typ

import typer

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    import collections.abc as cabc

_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSY_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_ALL_BOOL_VALUES: frozenset[str] = _TRUTHY_VALUES | _FALSY_VALUES


def _required_env(name: str, env: cabc.Mapping[str, str] | None = None) -> str:
    """Return the non-empty value of the required environment variable *name*.

    Reads *env* when given, so a caller can pass an explicit mapping instead of
    depending on ambient process state. Raises ``typer.Exit(2)`` when the
    variable is unset or empty.
    """
    source = os.environ if env is None else env
    value = source.get(name, "").strip()
    if value:
        return value
    typer.echo(f"Missing required environment variable: {name}", err=True)
    raise typer.Exit(2)


def _env_bool(
    name: str, *, default: bool, env: cabc.Mapping[str, str] | None = None
) -> bool:
    """Parse the environment variable *name* as a boolean.

    Reads *env* when given, so a caller can pass an explicit mapping instead
    of depending on ambient process state.
    Unset or empty values return *default*.  Recognized truthy values are
    ``1``, ``true``, ``yes``, ``on`` (case-insensitive); recognized falsy
    values are ``0``, ``false``, ``no``, ``off``.  Any other non-empty value
    is treated as a configuration error and raises ``typer.Exit(2)``.
    """
    source = os.environ if env is None else env
    value = source.get(name)
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
