"""Read a GitHub Actions ``if:`` condition as the conjunction it has to be.

A guard is asserted by what it requires, and a substring search cannot say
that. ``github.ref == 'refs/heads/main'`` is a substring of
``github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch'``,
which makes the ref optional and lets a dispatch from any branch through.
So a guard is split on its top-level ``&&`` into conjuncts, and a condition
with an ``||`` outside a string literal is refused outright rather than
reasoned about: every clause built on this reading requires each of its
terms, and a disjunction anywhere means some term is not required.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The term that binds a job or step to the trunk. A dispatch selects its own
#: ref, so a trigger filter does not bind it.
TRUNK_REF_TERM: typ.Final[str] = "github.ref == 'refs/heads/main'"
#: The expression delimiters GitHub strips from an ``if:`` when present.
_OPEN: typ.Final[str] = "${{"
_CLOSE: typ.Final[str] = "}}"


def _unwrapped(condition: str) -> str:
    """Return *condition* without its optional ``${{ }}`` wrapper."""
    text = condition.strip()
    if text.startswith(_OPEN) and text.endswith(_CLOSE):
        return text.removeprefix(_OPEN).removesuffix(_CLOSE).strip()
    return text


def _unquoted_positions(text: str) -> cabc.Iterator[tuple[int, int]]:
    """Yield each index outside a string literal, with its bracket depth.

    GitHub expression strings are single-quoted, and a quote inside one is
    doubled, which toggling on every quote handles without special cases.
    """
    quoted = False
    depth = 0
    for index, character in enumerate(text):
        if character == "'":
            quoted = not quoted
            continue
        if quoted:
            continue
        depth += {"(": 1, ")": -1}.get(character, 0)
        yield index, depth


def conjuncts(condition: str) -> list[str] | None:
    """Return the top-level ``&&`` terms of *condition*, or ``None``.

    ``None`` means the condition contains an ``||`` outside a string
    literal, at any depth, so no term of it can be said to be required.
    Whitespace inside each term is collapsed so a reflowed guard reads the
    same.

    Examples
    --------
    >>> conjuncts("${{ github.ref == 'refs/heads/main' && env.T != '' }}")
    ["github.ref == 'refs/heads/main'", "env.T != ''"]
    >>> conjuncts("github.ref == 'refs/heads/main' || true") is None
    True
    >>> conjuncts("contains(github.ref, '||')")
    ["contains(github.ref, '||')"]
    """
    text = _unwrapped(condition)
    cuts: list[int] = []
    for index, depth in _unquoted_positions(text):
        pair = text[index : index + 2]
        if pair == "||":
            return None
        if pair == "&&" and depth == 0:
            cuts.append(index)
    bounds = zip([0, *(cut + 2 for cut in cuts)], [*cuts, len(text)], strict=True)
    return [" ".join(text[start:end].split()) for start, end in bounds]
