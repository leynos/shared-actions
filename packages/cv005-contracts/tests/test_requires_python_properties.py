"""Property: a bare `X.Y` is accepted exactly when some patch satisfies the range.

Generated `>=3.13.a,<3.13.b` intervals and `!=` exclusions are compared with a
model that walks the patches directly, so the bound-driven candidate choice is
checked against ground truth over arbitrary intervals, not a fixed sample.
"""

from __future__ import annotations

from cv005_contracts.requires_python import _judge
from hypothesis import given
from hypothesis import strategies as st

PATCHES = range(0, 60)


@given(
    lower=st.integers(min_value=0, max_value=50),
    upper=st.integers(min_value=0, max_value=55),
    excluded=st.sets(st.integers(min_value=0, max_value=55), max_size=6),
)
def test_a_bare_minor_is_accepted_exactly_when_some_patch_satisfies(
    lower: int, upper: int, excluded: set[int]
) -> None:
    """Compare the clause with a direct walk over 3.13.0 to 3.13.59."""
    requirement = ",".join(
        [
            f">=3.13.{lower}",
            f"<3.13.{upper}",
            *(f"!=3.13.{p}" for p in sorted(excluded)),
        ]
    )
    some_patch = any(lower <= p < upper and p not in excluded for p in PATCHES)
    assert (_judge(requirement, "3.13") == []) is some_patch, requirement
