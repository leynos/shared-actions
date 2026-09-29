"""The publisher's concurrency group must be a string of the estate's spelling.

netsuke #825 found a group written as a mapping, which a reader taking any
value as a group passed. The rule reads the group as text and refuses every
other shape, while the two estate spellings stay accepted.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PUBLISHER
from cv005_contracts.concurrency import concurrency_violations
from cv005_contracts.loading import load_workflow

GROUP = "group: coverage-main-${{ github.ref }}"


def _with_group(replacement: str) -> list[str]:
    """Return the concurrency findings for the fixture with its group replaced."""
    assert GROUP in PUBLISHER
    return concurrency_violations(load_workflow(PUBLISHER.replace(GROUP, replacement)))


@pytest.mark.parametrize(
    "replacement",
    [
        # A mapping, a list, a number and a null are not a group name, and
        # none of them may pass because its text happens to be unlisted.
        "group:\n    name: coverage-main-${{ github.ref }}",
        "group: ['coverage-main-${{ github.ref }}']",
        "group: 7",
        "group:",
        "group: true",
    ],
)
def test_a_group_that_is_not_a_string_is_refused(replacement: str) -> None:
    """Only a string naming an estate group governs the upload."""
    found = _with_group(replacement)
    assert any("groups by" in item for item in found), found


@pytest.mark.parametrize(
    "replacement",
    [
        GROUP,
        "group: ${{ github.workflow }}-${{ github.ref }}",
        "group: ${{github.workflow}}-${{  github.ref  }}",
    ],
)
def test_a_string_group_in_an_estate_spelling_is_accepted(replacement: str) -> None:
    """The refusal above is about the shape, not the spelling."""
    found = _with_group(replacement)
    assert found == [], found
