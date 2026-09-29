"""A pairing may not exempt a matrix leg from the comparison it needs.

A declared pairing takes a lane leg out of the estate's selection comparison,
so it must prove the leg runs only in the cell it declares, that the cell
exists, and that legs standing in for one another cannot both run. These cases
drive each of those with a constructed tree, and a narrow case that passes.
"""

from __future__ import annotations

import typing as typ

import pytest
from cv005_contracts import violations
from cv005_contracts.exclusive import are_exclusive, contradict
from cv005_contracts.pairing import _matrix_cells
from matrix_fixtures import MATRIX_CONFIG, MATRIX_TREE, STRICT_LEG, write_repository

if typ.TYPE_CHECKING:
    from pathlib import Path

STRICT_GUARDS = "guards = [\"runner.os == 'Windows'\", \"matrix.features != ''\"]\n"


def _found(root: Path, config: str, texts: dict[str, str] | None = None) -> list[str]:
    """Return the findings for the matrix tree under a declaration."""
    tree = texts or MATRIX_TREE
    return [str(item) for item in violations(write_repository(root, tree, config))]


def test_a_matrix_pairing_without_guards_is_refused(tmp_path: Path) -> None:
    """Matrix values with nothing selecting the cell would exempt every cell."""
    assert STRICT_GUARDS in MATRIX_CONFIG
    found = _found(tmp_path, MATRIX_CONFIG.replace(STRICT_GUARDS, ""))
    assert any("declares matrix values but no guards" in item for item in found), found


def test_a_guardless_pairing_without_matrix_values_is_not_refused_for_it(
    tmp_path: Path,
) -> None:
    """The narrow case: with no matrix values there is no cell to select."""
    linux_guard = "guards = [\"runner.os == 'Linux'\"]\n"
    assert linux_guard in MATRIX_CONFIG
    found = _found(tmp_path, MATRIX_CONFIG.replace(linux_guard, ""))
    assert not any("no guards" in item for item in found), found


def test_legs_whose_guards_can_hold_together_do_not_count_as_one_ratchet(
    tmp_path: Path,
) -> None:
    """Different sets are not exclusive: both hold on a Windows cell with features."""
    lane = MATRIX_TREE["ci.yml"].replace(
        "runner.os == 'Windows' && matrix.features != ''", "matrix.features != ''"
    )
    config = MATRIX_CONFIG.replace(
        STRICT_GUARDS, "guards = [\"matrix.features != ''\"]\n"
    )
    found = _found(tmp_path, config, {**MATRIX_TREE, "ci.yml": lane})
    assert any("found 3" in item for item in found), found


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("runner.os == 'Linux'", "runner.os == 'Windows'", True),
        ("m.f == ''", "m.f != ''", True),
        ("m.f != ''", "m.f == ''", True),
        ("m.f == 'a'", "m.f == 'a'", False),
        ("m.f != 'a'", "m.f != 'b'", False),
        ("m.f == 'a'", "m.g == 'b'", False),
        ("m.f == 'a'", "m.f != 'b'", False),
        ("some.flag", "other.flag", False),
    ],
)
def test_two_terms_contradict_only_on_one_operand(
    first: str, second: str, *, expected: bool
) -> None:
    """The refusal is narrow: unrelated or compatible terms do not exclude."""
    assert contradict(first, second) is expected


def test_exclusivity_needs_every_pair_to_contradict() -> None:
    """Three legs are exclusive only if each pair is."""
    linux = frozenset({"runner.os == 'Linux'"})
    windows = frozenset({"runner.os == 'Windows'"})
    other = frozenset({"matrix.x == 'y'"})
    assert are_exclusive([linux, windows]) is True
    assert are_exclusive([linux, windows, other]) is False


def test_an_excluded_combination_is_not_a_cell() -> None:
    """GitHub removes excluded combinations from the product."""
    job: dict[str, object] = {
        "strategy": {
            "matrix": {
                "os": ["a", "b"],
                "f": ["x", "y"],
                "exclude": [{"os": "a", "f": "x"}],
            }
        }
    }
    cells = _matrix_cells(job) or []
    assert {"os": "a", "f": "x"} not in cells
    assert {"os": "a", "f": "y"} in cells


def test_an_include_restores_an_excluded_combination() -> None:
    """`include` applies after `exclude`, so it can bring a combination back."""
    job: dict[str, object] = {
        "strategy": {
            "matrix": {
                "os": ["a"],
                "f": ["x"],
                "exclude": [{"os": "a", "f": "x"}],
                "include": [{"os": "a", "f": "x", "extra": "1"}],
            }
        }
    }
    assert {"os": "a", "f": "x", "extra": "1"} in (_matrix_cells(job) or [])


def test_the_declared_tree_still_passes(tmp_path: Path) -> None:
    """The refusals above are not the fixture's doing."""
    assert STRICT_LEG in MATRIX_CONFIG
    assert _found(tmp_path, MATRIX_CONFIG) == []
