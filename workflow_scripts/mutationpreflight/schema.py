"""Validation primitives shared by the declaration and the mutmut table.

Each one either returns the shape the schema promises or raises
``DeclarationError`` naming the member, so a caller never has to guess which
part of a table was wrong.
"""

from __future__ import annotations

import typing as typ

from .models import DeclarationError


def refuse(where: str, expected: str) -> typ.NoReturn:
    """Refuse a member that is not the shape it must be."""
    message = f"{where} must be {expected}"
    raise DeclarationError(message)


def unknown_keys(
    table: dict[str, object], allowed: frozenset[str], where: str
) -> list[str]:
    """Return a finding for each key *table* carries that cannot be read."""
    return [
        f"{where} declares unrecognised key {key!r}; supported keys are "
        f"{sorted(allowed)}"
        for key in sorted(set(table) - allowed)
    ]


def refuse_unknown(
    table: dict[str, object], allowed: frozenset[str], where: str
) -> None:
    """Refuse the first key *table* carries that cannot be read."""
    problems = unknown_keys(table, allowed, where)
    if problems:
        raise DeclarationError(problems[0])


def strings(value: object, where: str) -> tuple[str, ...]:
    """Return *value* as a tuple of strings, refusing anything else."""
    if value is None:
        return ()
    valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    if not valid:
        refuse(where, "an array of strings")
    return tuple(typ.cast("list[str]", value))


def string(table: dict[str, object], key: str, where: str) -> str:
    """Return one optional string member, refusing a non-string."""
    value = table.get(key, "")
    if not isinstance(value, str):
        refuse(f"{where}.{key}", "a string")
    return value


def required_string(table: dict[str, object], key: str, where: str) -> str:
    """Return one required, non-empty string member."""
    value = string(table, key, where)
    if not value:
        refuse(f"{where}.{key}", "a non-empty string")
    return value


def required_strings(table: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    """Return one required, non-empty array of strings."""
    value = strings(table.get(key), f"{where}.{key}")
    if not value:
        refuse(f"{where}.{key}", "a non-empty array of strings")
    return value


def positive_int(value: object) -> bool:
    """Return whether *value* is a positive integer rather than a bool."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value > 0


def entry(raw: object, allowed: frozenset[str], where: str) -> dict[str, object]:
    """Return *raw* as an entry table carrying only supported keys."""
    if not isinstance(raw, dict):
        refuse(where, "a table")
    refuse_unknown(typ.cast("dict[str, object]", raw), allowed, where)
    return typ.cast("dict[str, object]", raw)
