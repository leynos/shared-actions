"""The two things a repository may declare beyond the estate's one shape.

Both are on the record in `.github/cv005.toml`, and neither passes silently:

- An **exception** waives one clause, citing the ruling that allows it and
  the reason. The waived findings are printed, and an exception that waives
  nothing is refused as stale, so it cannot outlive its cause.
- A **pairing** maps one lane leg to the publisher leg it ratchets against,
  where the two cannot be equal by inspection: a matrix job, or a Windows
  baseline written by a job of its own. The library checks that both legs
  exist and that the pair differs exactly as declared.

This module holds their shapes and their reading from parsed TOML.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ


class DeclarationError(ValueError):
    """Raised when a declared exception or pairing is malformed."""


@dc.dataclass(frozen=True, slots=True)
class Exemption:
    """One waived clause.

    Attributes
    ----------
    clause : str
        The clause identifier waived, such as `coverage.pull-request-lane`.
    ruling : str
        Where the exception was ruled, such as `leynos/whitaker#444`.
    reason : str
        Why the repository cannot meet the clause.
    requires : str
        Text a step of the publisher must run, such as `make coverage`, so
        the exception is grounded in what the repository does instead. Empty
        when nothing replaces the waived behaviour.

    """

    clause: str
    ruling: str
    reason: str
    requires: str = ""


@dc.dataclass(frozen=True, slots=True)
class Pairing:
    """One lane leg mapped to the publisher leg it ratchets against.

    A leg is named `workflow-file:job-id:step-name`.

    Attributes
    ----------
    lane : str
        The pull-request lane's generator leg.
    publisher : str
        The publisher's generator leg.
    matrix : dict[str, str]
        The values of the `matrix` keys the lane leg reads, so the leg's
        inputs are compared as they resolve for this matrix cell.
    differs : tuple[str, ...]
        The inputs or environment keys on which the two legs are declared to
        differ. Nothing else may differ, and each must.
    guards : tuple[str, ...]
        The whole `&&` terms, besides the pull-request guard, that select
        this leg. They are held exactly.

    """

    lane: str
    publisher: str
    matrix: dict[str, str] = dc.field(default_factory=dict)
    differs: tuple[str, ...] = ()
    guards: tuple[str, ...] = ()


def read_exemptions(raw: object) -> tuple[Exemption, ...]:
    """Read the `[[exception]]` tables.

    Parameters
    ----------
    raw : object
        The parsed `exception` value, or None when absent.

    Returns
    -------
    tuple[Exemption, ...]
        The declared exceptions.

    Raises
    ------
    DeclarationError
        If a table is not a mapping of the known string keys, omits its
        clause, ruling or reason, or waives one clause twice.

    Examples
    --------
    >>> read_exemptions([{"clause": "a.b", "ruling": "x#1", "reason": "why"}])
    (Exemption(clause='a.b', ruling='x#1', reason='why', requires=''),)

    """
    found = [
        _table(item, "exception", {"clause", "ruling", "reason"}, {"requires"})
        for item in _tables(raw, "exception")
    ]
    for table in found:
        _require_text(table, ("clause", "ruling", "reason"))
    _refuse_repeats([table["clause"] for table in found], "exception waives a clause")
    return tuple(Exemption(**table) for table in found)


def read_pairings(raw: object) -> tuple[Pairing, ...]:
    """Read the `[[pairing]]` tables.

    Parameters
    ----------
    raw : object
        The parsed `pairing` value, or None when absent.

    Returns
    -------
    tuple[Pairing, ...]
        The declared pairings.

    Raises
    ------
    DeclarationError
        If a table is malformed, omits its lane or publisher, or names the
        same lane leg twice.

    """
    found = [_pairing(item) for item in _tables(raw, "pairing")]
    _refuse_repeats([pairing.lane for pairing in found], "pairing names a lane leg")
    return tuple(found)


def _pairing(item: object) -> Pairing:
    """Read one `[[pairing]]` table into a `Pairing`."""
    table = _table(item, "pairing", {"lane", "publisher"}, _PAIRING_OPTIONAL)
    _require_text(table, ("lane", "publisher"))
    return Pairing(
        lane=table["lane"],
        publisher=table["publisher"],
        matrix=_strings_by_key(table.get("matrix", {}), "matrix"),
        differs=tuple(_strings(table.get("differs", []), "differs")),
        guards=tuple(_strings(table.get("guards", []), "guards")),
    )


def _refuse_repeats(names: list[str], what: str) -> None:
    """Refuse a name that appears more than once, saying `what` repeated it."""
    if repeated := sorted({name for name in names if names.count(name) > 1}):
        message = f"{what} twice: {repeated}"
        raise DeclarationError(message)


_PAIRING_OPTIONAL: typ.Final[set[str]] = {"matrix", "differs", "guards"}


def _tables(raw: object, name: str) -> list[object]:
    """Return an array of tables, empty when absent."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        message = f"{name} must be an array of tables ([[{name}]])"
        raise DeclarationError(message)
    return raw


def _table(
    item: object, name: str, required: set[str], optional: set[str]
) -> dict[str, typ.Any]:
    """Return one table's keys, refusing an unknown or missing key."""
    if not isinstance(item, dict):
        message = f"each [[{name}]] must be a table"
        raise DeclarationError(message)
    if unknown := sorted(set(item) - required - optional):
        message = f"[[{name}]] names unknown keys: {unknown}"
        raise DeclarationError(message)
    if missing := sorted(required - set(item)):
        message = f"[[{name}]] must set {missing}"
        raise DeclarationError(message)
    return typ.cast("dict[str, typ.Any]", item)


def _require_text(table: dict[str, typ.Any], keys: tuple[str, ...]) -> None:
    """Refuse a key whose value is not a non-empty string."""
    for key in keys:
        if not _is_text(table[key]):
            message = f"{key} must be a non-empty string"
            raise DeclarationError(message)


def _is_text(value: object) -> bool:
    """Return whether a value is a string with something in it."""
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object, name: str) -> list[str]:
    """Return a list of non-empty strings, or refuse."""
    if isinstance(value, list) and all(_is_text(item) for item in value):
        return typ.cast("list[str]", value)
    message = f"{name} must be a list of non-empty strings"
    raise DeclarationError(message)


def _strings_by_key(value: object, name: str) -> dict[str, str]:
    """Return a mapping of strings to strings, or refuse."""
    if isinstance(value, dict) and all(
        _is_string_pair(key, item) for key, item in value.items()
    ):
        return typ.cast("dict[str, str]", value)
    message = f"{name} must map names to strings"
    raise DeclarationError(message)


def _is_string_pair(key: object, item: object) -> bool:
    """Return whether a mapping entry is a string keyed to a string."""
    return isinstance(key, str) and isinstance(item, str)
