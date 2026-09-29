"""A declared exception waives one clause, on the record, or is itself a finding.

The make tree measures by `make coverage`, so three clauses find nothing to
measure. The cases show each waiver taking effect for its clause alone, being
printed, and being refused as stale, unknown or ungrounded.
"""

from __future__ import annotations

import typing as typ

import pytest
from cv005_contracts import ConfigError, violations
from cv005_contracts.api import report
from cv005_contracts.cli import EXIT_CLEAN, EXIT_VIOLATIONS, check
from cv005_contracts.config import config_from_mapping
from matrix_fixtures import MAKE_CONFIG, MAKE_TREE, WAIVED_CLAUSES, write_repository

if typ.TYPE_CHECKING:
    from pathlib import Path

BARE_CONFIG: typ.Final[str] = 'repository = "leynos/example"\n'


def _exception(clause: str, extra: str = "") -> str:
    """Return one `[[exception]]` table for a clause."""
    return (
        f'\n[[exception]]\nclause = "{clause}"\nruling = "leynos/example#444"\n'
        f'reason = "Coverage comes from `make coverage`."\n{extra}'
    )


def _clauses(root: Path, config: str) -> list[str]:
    """Return the clause of every finding that stands."""
    found = violations(write_repository(root, MAKE_TREE, config))
    return [item.clause for item in found]


def test_the_tree_is_refused_until_its_clauses_are_waived(tmp_path: Path) -> None:
    """Without the declaration, each clause finds nothing to measure."""
    assert sorted(_clauses(tmp_path, BARE_CONFIG)) == sorted(WAIVED_CLAUSES)


def test_the_declared_exceptions_waive_exactly_their_clauses(tmp_path: Path) -> None:
    """Every finding waived leaves the tree clean."""
    assert _clauses(tmp_path, MAKE_CONFIG) == []


@pytest.mark.parametrize("kept", WAIVED_CLAUSES)
def test_an_exception_waives_its_own_clause_alone(tmp_path: Path, kept: str) -> None:
    """Waiving the other two leaves this clause's finding standing."""
    config = BARE_CONFIG + "".join(
        _exception(clause) for clause in WAIVED_CLAUSES if clause != kept
    )
    assert _clauses(tmp_path, config) == [kept]


def test_an_exception_does_not_waive_a_whole_family(tmp_path: Path) -> None:
    """Waiving one `coverage` clause leaves the other `coverage` clauses held."""
    config = BARE_CONFIG + _exception("coverage.selection-parity")
    clauses = _clauses(tmp_path, config)
    assert "coverage.pull-request-lane" in clauses, clauses
    assert "coverage.selection-parity" not in clauses, clauses


def test_a_waived_finding_is_reported_with_its_ruling(tmp_path: Path) -> None:
    """The report carries each waiver and what it waived, for printing."""
    result = report(write_repository(tmp_path, MAKE_TREE, MAKE_CONFIG))
    waived = {waiver.exemption.clause: waiver.findings for waiver in result.waivers}
    assert set(waived) == set(WAIVED_CLAUSES), waived
    assert all(findings for findings in waived.values()), waived


def test_the_command_prints_every_declared_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A holding exception is never silent, and the run still exits clean."""
    root = write_repository(tmp_path, MAKE_TREE, MAKE_CONFIG)
    assert check(repository=root) == EXIT_CLEAN
    out = capsys.readouterr().out
    for clause in WAIVED_CLAUSES:
        assert f"exception {clause} [leynos/example#444]" in out, out
    assert "waived: the publisher must generate coverage" in out, out


def test_an_exception_that_waives_nothing_is_refused(tmp_path: Path) -> None:
    """A stale exception must not outlive the finding it excused."""
    config = MAKE_CONFIG + _exception("coverage.second-writer")
    found = violations(write_repository(tmp_path, MAKE_TREE, config))
    assert [(i.clause, "waives nothing" in i.message) for i in found] == [
        ("exception", True)
    ], found


def test_an_exception_naming_no_clause_is_refused(tmp_path: Path) -> None:
    """A misspelt clause waives nothing, and says so rather than passing."""
    found = violations(
        write_repository(tmp_path, MAKE_TREE, MAKE_CONFIG + _exception("coverage.nope"))
    )
    assert [(i.clause, "is not a clause" in i.message) for i in found] == [
        ("exception", True)
    ], found


def test_an_exception_needing_a_command_the_publisher_does_not_run_is_refused(
    tmp_path: Path,
) -> None:
    """The exception is grounded in what the publisher runs instead."""
    texts = {
        **MAKE_TREE,
        "coverage-main.yml": MAKE_TREE["coverage-main.yml"].replace(
            "run: make coverage", "run: make test"
        ),
    }
    found = violations(write_repository(tmp_path, texts, MAKE_CONFIG))
    assert {i.clause for i in found} == {"exception"}, found
    assert all("`make coverage`" in i.message for i in found), found


def test_an_exception_for_a_family_that_did_not_run_is_not_stale(
    tmp_path: Path,
) -> None:
    """Selecting other families cannot judge a clause it never ran."""
    root = write_repository(tmp_path, MAKE_TREE, MAKE_CONFIG)
    found = violations(root, only=frozenset({"publisher"}))
    assert [i.clause for i in found] == [], found


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"exception": [{"clause": "a.b", "ruling": "x", "reason": "y"}] * 2}, "twice"),
        ({"exception": [{"clause": "a.b", "reason": "y"}]}, "ruling"),
        ({"exception": [{"clause": "a.b", "ruling": "x", "reason": " "}]}, "reason"),
        (
            {"exception": [{"clause": "a.b", "ruling": "x", "reason": "y", "why": 1}]},
            "unknown",
        ),
        ({"exception": {"clause": "a.b"}}, "array of tables"),
        ({"exceptions": []}, "unknown keys"),
    ],
)
def test_a_malformed_exception_is_refused(
    raw: dict[str, object], fragment: str
) -> None:
    """The declaration cannot be misspelt or left half-written."""
    with pytest.raises(ConfigError, match=fragment):
        config_from_mapping({"repository": "leynos/example", **raw})


def test_the_command_fails_on_a_stale_exception(tmp_path: Path) -> None:
    """A stale exception is a violation, not a note."""
    root = write_repository(
        tmp_path, MAKE_TREE, MAKE_CONFIG + _exception("coverage.second-writer")
    )
    assert check(repository=root) == EXIT_VIOLATIONS
