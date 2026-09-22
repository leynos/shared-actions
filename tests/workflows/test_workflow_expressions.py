"""The ``if:`` reader, driven on conditions written for each case.

This repository's publisher guard is a plain two-term conjunction, so over it
a substring search and this reader agree. These cases are the ones where they
differ.

Run via ``make test``.
"""

from __future__ import annotations

import pytest

from .workflow_expressions import TRUNK_REF_TERM, conjuncts

#: The publisher's credential term, as the contract requires it.
CREDENTIAL_TERM = "env.CS_ACCESS_TOKEN != ''"


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        pytest.param(
            f"{TRUNK_REF_TERM} && {CREDENTIAL_TERM}",
            [TRUNK_REF_TERM, CREDENTIAL_TERM],
            id="bare",
        ),
        pytest.param(
            f"${{{{ {TRUNK_REF_TERM} && {CREDENTIAL_TERM} }}}}",
            [TRUNK_REF_TERM, CREDENTIAL_TERM],
            id="wrapped",
        ),
        pytest.param(
            f"{CREDENTIAL_TERM}\n  &&   {TRUNK_REF_TERM}",
            [CREDENTIAL_TERM, TRUNK_REF_TERM],
            id="reflowed",
        ),
        pytest.param(
            f"{TRUNK_REF_TERM} && ({CREDENTIAL_TERM} && always())",
            [TRUNK_REF_TERM, f"({CREDENTIAL_TERM} && always())"],
            id="nested-conjunction-is-one-term",
        ),
        pytest.param(
            "github.head_ref == 'a && b'",
            ["github.head_ref == 'a && b'"],
            id="quoted-and",
        ),
        pytest.param(
            "github.head_ref == 'it''s || fine'",
            ["github.head_ref == 'it''s || fine'"],
            id="quoted-or-with-doubled-quote",
        ),
    ],
)
def test_a_conjunction_is_split_into_its_terms(
    condition: str, expected: list[str]
) -> None:
    """Only a top-level ``&&`` outside a string literal separates terms."""
    assert conjuncts(condition) == expected, condition


@pytest.mark.parametrize(
    "condition",
    [
        pytest.param(
            f"{TRUNK_REF_TERM} && {CREDENTIAL_TERM}"
            " || github.event_name == 'workflow_dispatch'",
            id="appended-dispatch",
        ),
        pytest.param(
            f"{TRUNK_REF_TERM} && ({CREDENTIAL_TERM} || true)",
            id="nested",
        ),
        pytest.param(
            f"${{{{ {TRUNK_REF_TERM} || {CREDENTIAL_TERM} }}}}",
            id="wrapped",
        ),
    ],
)
def test_an_unquoted_disjunction_is_refused(condition: str) -> None:
    """With an ``||`` anywhere, no term is required, so there is no reading.

    The first case is the sweep mutation: both substrings survive it, and a
    dispatch from any branch then uploads.
    """
    assert conjuncts(condition) is None, condition
