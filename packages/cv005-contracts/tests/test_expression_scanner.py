"""Hypothesis properties of the quote-aware expression scanner.

The scanner is the one reader that decides where a `${{ }}` expression
ends, and a wrong end hides whatever follows it from the secret sweeps. The
property builds texts from known pieces, so the expected bodies are known by
construction rather than by a second scanner: plain text that contains no
`${{`, and expression bodies whose single-quoted literals may hold `}}`,
doubled quotes and `${{`.
"""

from __future__ import annotations

import typing as typ

from cv005_contracts.expressions import expression_bodies
from hypothesis import given
from hypothesis import strategies as st

#: Plain text outside any expression: never `${{`, so it opens nothing.
PLAIN: typ.Final = st.text(alphabet="ab }{$'\n", max_size=8).filter(
    lambda text: "${{" not in text and not text.endswith(("$", "${"))
)

#: A single-quoted literal inside an expression, with any closers inside.
LITERAL: typ.Final = st.lists(
    st.sampled_from(["x", "}}", "''", "${{", " ", "}"]), max_size=4
).map(lambda parts: "'" + "".join(parts) + "'")

#: A bare token inside an expression: no quote and no `}}`.
TOKEN: typ.Final = st.sampled_from(["a", " ", "==", "(b)", "secrets", "}", "{"])

#: An expression body: tokens and literals, never a bare `}}`.
BODY: typ.Final = (
    st.lists(st.one_of(TOKEN, LITERAL), max_size=5)
    .map("".join)
    .filter(lambda body: "}}" not in _unquoted(body) and not body.endswith("}"))
)


def _unquoted(body: str) -> str:
    """Return a body with its single-quoted literals removed."""
    kept, quoted = [], False
    for char in body:
        if char == "'":
            quoted = not quoted
        elif not quoted:
            kept.append(char)
    return "".join(kept)


@given(st.lists(st.tuples(PLAIN, BODY), max_size=4), PLAIN)
def test_the_scanner_finds_each_body_whole(
    pieces: list[tuple[str, str]], tail: str
) -> None:
    """Every expression is read to its real end, whatever its literals hold."""
    text = "".join(f"{plain}${{{{{body}}}}}" for plain, body in pieces) + tail
    assert expression_bodies(text) == [body for _, body in pieces]


@given(PLAIN, BODY)
def test_an_open_expression_reads_to_the_end(plain: str, body: str) -> None:
    """An expression never closed runs to the end of the text, failing closed."""
    assert expression_bodies(f"{plain}${{{{{body}") == [body]
