"""A declared pairing maps a lane leg to a publisher leg, and is held to it.

Each case changes one thing in the matrix tree or its declaration and asserts
on the finding that must follow, so deleting the check fails the case. The
compliant tree passes only with its three pairings; without them the estate
rule refuses every leg, and the first two cases show both.
"""

from __future__ import annotations

import typing as typ

import pytest
from cv005_contracts import ConfigError, violations
from cv005_contracts.config import config_from_mapping
from matrix_fixtures import (
    BASELINE_LEG,
    MATRIX_CONFIG,
    MATRIX_TREE,
    STRICT_LEG,
    WINDOWS_LEG,
    write_repository,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

BARE_CONFIG: typ.Final[str] = 'repository = "leynos/example"\n'


def _found(
    root: Path,
    *,
    config: str = MATRIX_CONFIG,
    old: tuple[str, str] | None = None,
    workflow: str = "ci.yml",
) -> list[str]:
    """Return the findings for the matrix tree, with one replacement made."""
    texts = dict(MATRIX_TREE)
    if old is not None:
        assert texts[workflow].count(old[0]) == 1, old
        texts[workflow] = texts[workflow].replace(*old)
    return [str(item) for item in violations(write_repository(root, texts, config))]


def _config_with(replaced: tuple[str, str]) -> str:
    """Return the declaration with one exact replacement."""
    assert MATRIX_CONFIG.count(replaced[0]) == 1, replaced
    return MATRIX_CONFIG.replace(*replaced)


def test_the_declared_tree_passes(tmp_path: Path) -> None:
    """Three legs, three pairings, one baseline written twice: nothing is found."""
    assert _found(tmp_path) == []


def test_without_the_declaration_the_estate_rule_refuses_every_leg(
    tmp_path: Path,
) -> None:
    """The conditions, the three ratchets and the matrix inputs are all refused."""
    found = _found(tmp_path, config=BARE_CONFIG)
    clauses = {item.split(":", 1)[0] for item in found}
    assert clauses == {
        "coverage.pull-request-lane",
        "coverage.lane-hardening",
        "coverage.selection-parity",
    }, found


def test_an_unmapped_leg_is_refused(tmp_path: Path) -> None:
    """Dropping one pairing leaves that leg to the estate rule, which refuses it."""
    kept = MATRIX_CONFIG[: MATRIX_CONFIG.index('[[pairing]]\nlane = "' + STRICT_LEG)]
    found = _found(tmp_path, config=kept)
    assert any("selection differs" in item for item in found), found
    assert any("may carry only the pull-request guard" in item for item in found), found
    assert any("found 2" in item for item in found), found


def test_a_pairing_naming_a_lane_leg_that_does_not_exist_is_refused(
    tmp_path: Path,
) -> None:
    """A renamed step would otherwise leave its pairing describing nothing."""
    config = _config_with(
        (f'lane = "{WINDOWS_LEG}"', 'lane = "ci.yml:build-test:Gone"')
    )
    found = _found(tmp_path, config=config)
    assert any("lane leg ci.yml:build-test:Gone does not exist" in i for i in found), (
        found
    )


def test_a_pairing_naming_a_publisher_leg_that_does_not_exist_is_refused(
    tmp_path: Path,
) -> None:
    """The baseline job renamed away leaves its lane legs unpaired."""
    gone = "coverage-main.yml:coverage-baseline:Generate coverage"
    found = _found(tmp_path, config=MATRIX_CONFIG.replace(BASELINE_LEG, gone))
    assert any(f"publisher leg {gone} does not exist" in item for item in found), found


def test_a_difference_the_pairing_does_not_declare_is_refused(tmp_path: Path) -> None:
    """The baseline moved to other features, so the default-features leg differs."""
    found = _found(
        tmp_path,
        old=(
            "          features: base\n          with-default-features: true\n",
            "          features: other\n          with-default-features: true\n",
        ),
        workflow="coverage-main.yml",
    )
    assert any("differs from" in item and "'features'" in item for item in found), found


def test_a_difference_the_pairing_declares_but_the_legs_lack_is_refused(
    tmp_path: Path,
) -> None:
    """A stale `differs` would excuse a difference that returns later."""
    config = _config_with(
        (
            'differs = ["features", "with-default-features"]',
            'differs = ["features", "with-default-features", "all-features"]',
        )
    )
    found = _found(tmp_path, config=config)
    assert any("declares a difference on 'all-features'" in item for item in found), (
        found
    )


def test_a_matrix_cell_the_job_does_not_have_is_refused(tmp_path: Path) -> None:
    """Values that no cell of the job's matrix carries prove nothing."""
    config = _config_with(('features = "strict"', 'features = "lenient"'))
    found = _found(tmp_path, config=config)
    assert any("has no cell with" in item for item in found), found


def test_a_matrix_value_the_leg_never_reads_is_refused(tmp_path: Path) -> None:
    """An unused declared value is stale, or names the wrong leg's cell."""
    config = _config_with(
        (
            'with-default-features = "true"',
            'with-default-features = "true"\nplatform = "windows"',
        )
    )
    found = _found(tmp_path, config=config)
    assert any("never reads" in item and "platform" in item for item in found), found


def test_a_matrix_value_the_pairing_does_not_give_is_refused(tmp_path: Path) -> None:
    """An input the leg reads from the matrix cannot be compared unresolved."""
    config = _config_with(('[pairing.matrix]\nwith-default-features = "true"\n', ""))
    found = _found(tmp_path, config=config)
    assert any("does not give" in item for item in found), found


def test_the_declared_conditions_are_held_exactly(tmp_path: Path) -> None:
    """A leg carrying a condition other than its declared ones is refused."""
    found = _found(
        tmp_path,
        old=(
            "runner.os == 'Windows' && matrix.features == ''\n",
            "runner.os == 'Windows' && matrix.features == '' && github.actor != 'x'\n",
        ),
    )
    assert any("its condition is" in item for item in found), found


def test_a_condition_outside_the_pairing_is_still_refused_by_the_lane_rules(
    tmp_path: Path,
) -> None:
    """The pairing widens the allowed terms only by those it declares."""
    found = _found(
        tmp_path,
        old=(
            "runner.os == 'Linux' && github.event_name",
            "runner.os == 'Linux' && github.actor != 'x' && github.event_name",
        ),
    )
    assert any("may carry only the pull-request guard" in item for item in found), found


def test_legs_sharing_their_selecting_conditions_do_not_stand_in_for_each_other(
    tmp_path: Path,
) -> None:
    """Two legs that may both run in one cell ratchet twice, and the job is refused."""
    config = _config_with(
        (
            "\"matrix.features != ''\"",
            "\"matrix.features == ''\"",
        )
    )
    texts = dict(MATRIX_TREE)
    texts["ci.yml"] = texts["ci.yml"].replace(
        "matrix.features != ''", "matrix.features == ''"
    )
    found = [str(i) for i in violations(write_repository(tmp_path, texts, config))]
    assert any("found 3" in item for item in found), found


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ([{"lane": "a:b:c", "publisher": "d:e:f"}] * 2, "twice"),
        ([{"lane": "a:b:c"}], "publisher"),
        ([{"lane": "", "publisher": "d:e:f"}], "lane"),
        ([{"lane": "a:b:c", "publisher": "d:e:f", "matrx": {}}], "unknown"),
        ([{"lane": "a:b:c", "publisher": "d:e:f", "matrix": {"k": 1}}], "matrix"),
        ([{"lane": "a:b:c", "publisher": "d:e:f", "guards": ["", "x"]}], "guards"),
        ([{"lane": "a:b:c", "publisher": "d:e:f", "differs": "features"}], "differs"),
        ({"lane": "a:b:c"}, "array of tables"),
    ],
)
def test_a_malformed_pairing_is_refused(raw: object, fragment: str) -> None:
    """A pairing cannot be misspelt, left half-written or declared twice."""
    with pytest.raises(ConfigError, match=fragment):
        config_from_mapping({"repository": "leynos/example", "pairing": raw})
