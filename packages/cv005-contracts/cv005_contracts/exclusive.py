"""Decide whether conditions can never hold together.

Two legs of one matrix job count as one ratchet only if at most one of them
runs in any cell. Different sets of terms do not show that: `a == 'x'` and
`b == 'y'` both hold in a cell where a is x and b is y. Two condition sets are
mutually exclusive here only when some pair of their terms contradicts, which
is a comparison of the same operand to two different literals, or the same
literal with `==` in one and `!=` in the other.
"""

from __future__ import annotations

import itertools
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_COMPARISON: typ.Final[re.Pattern[str]] = re.compile(r"^(.+?)\s*(==|!=)\s*(.+)$")


def contradict(first: str, second: str) -> bool:
    """Return whether two terms cannot both be true.

    Examples
    --------
    >>> contradict("runner.os == 'Linux'", "runner.os == 'Windows'")
    True
    >>> contradict("m.f == ''", "m.f != ''")
    True
    >>> contradict("m.f == ''", "runner.os == 'Windows'")
    False

    """
    left, right = _parse(first), _parse(second)
    if left is None or right is None:
        return False
    (operand, op, literal), (other, other_op, other_literal) = left, right
    if operand != other:
        return False
    if op == other_op == "==":
        return literal != other_literal
    return op != other_op and literal == other_literal


def _parse(term: str) -> tuple[str, str, str] | None:
    """Split a comparison into operand, operator and literal, or None."""
    match = _COMPARISON.match(term)
    return None if match is None else (match[1], match[2], match[3])


def are_exclusive(selectors: cabc.Sequence[frozenset[str]]) -> bool:
    """Return whether every pair of condition sets contradicts somewhere.

    Examples
    --------
    >>> linux = frozenset({"runner.os == 'Linux'"})
    >>> windows = frozenset({"runner.os == 'Windows'", "m.f == ''"})
    >>> are_exclusive([linux, windows])
    True
    >>> are_exclusive([linux, frozenset({"m.f != ''"})])
    False

    """
    return all(
        any(contradict(a, b) for a in first for b in second)
        for first, second in itertools.combinations(selectors, 2)
    )
