"""Validation primitives shared by the declaration and the mutmut table.

Each one either returns the shape the schema promises or raises
``DeclarationError`` naming the member, so a caller never has to guess which
part of a table was wrong.
"""

from __future__ import annotations

import typing as typ

from .models import DeclarationError


def refuse(where: str, expected: str) -> typ.NoReturn:
    """Refuse a member that is not the shape it must be.

    Parameters
    ----------
    where : str
        The member being read, spelled as the declaration spells it.
    expected : str
        What the member would have to be.

    Raises
    ------
    DeclarationError
        Always, naming *where* and *expected*.

    """
    message = f"{where} must be {expected}"
    raise DeclarationError(message)


def unknown_keys(
    table: dict[str, object], allowed: frozenset[str], where: str
) -> list[str]:
    """Return a finding for each key *table* carries that cannot be read.

    Parameters
    ----------
    table : dict[str, object]
        The table whose keys are reviewed.
    allowed : frozenset[str]
        The keys the table may carry.
    where : str
        The table's name, used in each finding.

    Returns
    -------
    list[str]
        One finding per unexpected key, sorted by key. A table carrying only
        supported keys yields an empty list.

    """
    return [
        f"{where} declares unrecognized key {key!r}; supported keys are "
        f"{sorted(allowed)}"
        for key in sorted(set(table) - allowed)
    ]


def refuse_unknown(
    table: dict[str, object], allowed: frozenset[str], where: str
) -> None:
    """Refuse the first key *table* carries that cannot be read.

    Parameters
    ----------
    table : dict[str, object]
        The table whose keys are reviewed.
    allowed : frozenset[str]
        The keys the table may carry.
    where : str
        The table's name, used in the finding.

    Raises
    ------
    DeclarationError
        When the table carries at least one unexpected key.

    """
    problems = unknown_keys(table, allowed, where)
    if problems:
        raise DeclarationError(problems[0])


def strings(value: object, where: str) -> tuple[str, ...]:
    """Return *value* as a tuple of strings, refusing anything else.

    Parameters
    ----------
    value : object
        The member as the parsed document holds it. ``None`` means the member
        is absent and yields an empty tuple, so an optional array and an empty
        one are the same to every caller.
    where : str
        The member's name, used in the finding.

    Returns
    -------
    tuple[str, ...]
        The member's strings, in document order.

    Raises
    ------
    DeclarationError
        When *value* is neither ``None`` nor a list of strings.

    """
    if value is None:
        return ()
    valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    if not valid:
        refuse(where, "an array of strings")
    return tuple(typ.cast("list[str]", value))


def string(table: dict[str, object], key: str, where: str) -> str:
    """Return one optional string member, refusing a non-string.

    Parameters
    ----------
    table : dict[str, object]
        The table holding the member.
    key : str
        The member's key.
    where : str
        The table's name, used in the finding.

    Returns
    -------
    str
        The member's value, or the empty string when it is absent. An empty
        string is how every caller spells "not declared".

    Raises
    ------
    DeclarationError
        When the member is present and is not a string.

    """
    value = table.get(key, "")
    if not isinstance(value, str):
        refuse(f"{where}.{key}", "a string")
    return value


def _required[Value](value: Value, where: str, key: str, expected: str) -> Value:
    """Return *value*, refusing an empty member named ``where.key``."""
    if not value:
        refuse(f"{where}.{key}", expected)
    return value


def required_string(table: dict[str, object], key: str, where: str) -> str:
    """Return one required, non-empty string member.

    Parameters
    ----------
    table : dict[str, object]
        The table holding the member.
    key : str
        The member's key.
    where : str
        The table's name, used in the finding.

    Returns
    -------
    str
        The member's value.

    Raises
    ------
    DeclarationError
        When the member is absent, empty, or not a string.

    """
    return _required(string(table, key, where), where, key, "a non-empty string")


def required_strings(table: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    """Return one required, non-empty array of strings.

    Parameters
    ----------
    table : dict[str, object]
        The table carrying the required array.
    key : str
        The key naming that array.
    where : str
        The declaration's name, used in the finding.

    Returns
    -------
    tuple[str, ...]
        The array's strings, in document order.

    Raises
    ------
    DeclarationError
        When the member is absent, empty, or not an array of strings.

    """
    value = strings(table.get(key), f"{where}.{key}")
    return _required(value, where, key, "a non-empty array of strings")


def positive_int(value: object) -> bool:
    """Return whether *value* is a positive integer rather than a bool.

    Parameters
    ----------
    value : object
        The parsed member.

    Returns
    -------
    bool
        ``True`` only for an ``int`` greater than zero. A ``bool`` is refused
        because ``True`` is an ``int`` to Python and would otherwise pass as
        the count one.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value > 0


def entry(raw: object, allowed: frozenset[str], where: str) -> dict[str, object]:
    """Return *raw* as an entry table carrying only supported keys.

    Parameters
    ----------
    raw : object
        One array entry as the parsed document holds it.
    allowed : frozenset[str]
        The keys the entry may carry.
    where : str
        The entry's name, used in the finding.

    Returns
    -------
    dict[str, object]
        The entry, once it is known to be a table of supported keys.

    Raises
    ------
    DeclarationError
        When *raw* is not a table or carries an unexpected key.

    """
    if not isinstance(raw, dict):
        refuse(where, "a table")
    refuse_unknown(typ.cast("dict[str, object]", raw), allowed, where)
    return typ.cast("dict[str, object]", raw)
