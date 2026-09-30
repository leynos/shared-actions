"""Refusal and narrow cases for the artefact-path guard.

A lane's `publish-artefact: 'false'` is defeated by any `upload-artifact` step
whose `path` could select the report. Each breaching entry below publishes the
report without naming it, and each narrow entry is a common upload that cannot
reach it; the pair shows the guard fires on the case and stays quiet off it.
"""

from __future__ import annotations

import pytest
from contract_fixtures import mutate, parse_tree
from cv005_contracts.hardening import lane_hardening_violations
from cv005_contracts.report_paths import (
    could_hold_the_report,
    entries_of,
)

REPORT = "lcov.info"
LANE_STEP = "      - name: Test and Measure Coverage\n"

#: Entries that select the report, or cannot be shown not to.
BREACHING = [
    pytest.param("lcov.info", id="the report by name"),
    pytest.param("nested/lcov.info", id="a path ending in the report's name"),
    pytest.param(".", id="the workspace"),
    pytest.param("./", id="the workspace with a slash"),
    pytest.param("..", id="the workspace's parent"),
    pytest.param("../sibling", id="a path climbing out"),
    pytest.param("*.info", id="a glob over the extension"),
    pytest.param("**/*.info", id="a recursive glob"),
    pytest.param("l?ov.info", id="a glob with a wildcard character"),
    pytest.param("[l]cov.info", id="a glob with a character class"),
    pytest.param("{lcov,other}.info", id="a brace group naming it"),
    pytest.param("*", id="every workspace file"),
    pytest.param("/", id="the filesystem root"),
    pytest.param("/home/runner/work", id="an absolute path above the workspace"),
    pytest.param(
        "/tmp/../home/runner/work",  # noqa: S108
        id="a scratch path that climbs out",
    ),
    pytest.param("~", id="the home directory"),
    pytest.param("${{ github.workspace }}", id="the workspace expression"),
    pytest.param("${{ github.workspace }}/dist", id="an expression inside a path"),
    pytest.param(
        "${{ x && '/tmp/a.log' || github.workspace }}",
        id="an alternative that yields the workspace",
    ),
    pytest.param("${{ x && '/tmp/a.log' || ", id="an unterminated expression"),
    pytest.param("${{ (x && '/tmp/a.log' }}", id="an unbalanced expression"),
    pytest.param("${{ x && 'dist/' }}", id="an expression yielding a relative path"),
]

#: Entries that cannot reach the report at the workspace root.
NARROW = [
    pytest.param("dist/", id="a directory beside the report"),
    pytest.param("target/debug/*.log", id="a glob over other files"),
    pytest.param("**/proptest-regressions/**", id="a recursive glob over other files"),
    pytest.param("logs/*.txt", id="a glob in another directory"),
    pytest.param("!lcov.info", id="a negation"),
    pytest.param("/tmp/a.log", id="a scratch file"),  # noqa: S108
    pytest.param("/tmp/logs/", id="a scratch directory"),  # noqa: S108
    pytest.param("${{ x && '/tmp/a.log' || '' }}", id="a scratch expression"),
    pytest.param("{a,b}/*.txt", id="a brace group over other files"),
]


@pytest.mark.parametrize("entry", BREACHING)
def test_an_entry_that_could_hold_the_report_is_refused(entry: str) -> None:
    """Scenario: an artefact path selects the report without a plain name."""
    assert could_hold_the_report(entry, REPORT)


@pytest.mark.parametrize("entry", NARROW)
def test_an_entry_that_cannot_hold_the_report_is_allowed(entry: str) -> None:
    """Scenario: a common upload that cannot reach the report stays allowed."""
    assert not could_hold_the_report(entry, REPORT)


@pytest.mark.parametrize(
    ("entry", "report"),
    [
        ("target", "target/lcov.info"),
        ("target/", "./target/lcov.info"),
        ("target/*.info", "target/lcov.info"),
        ("t*", "target/lcov.info"),
        ("**/lcov.info", "a/b/lcov.info"),
    ],
)
def test_a_directory_or_glob_holding_a_nested_report_is_refused(
    entry: str, report: str
) -> None:
    """Scenario: the report sits below the workspace root, in a directory."""
    assert could_hold_the_report(entry, report)


@pytest.mark.parametrize(
    ("entry", "report"),
    [
        ("target/debug", "target/lcov.info"),
        ("dist", "target/lcov.info"),
        ("target/*.log", "target/lcov.info"),
        ("*.info", "target/lcov.info"),
    ],
)
def test_a_sibling_directory_or_glob_of_a_nested_report_is_allowed(
    entry: str, report: str
) -> None:
    """Scenario: the entry sits beside the nested report, not over it."""
    assert not could_hold_the_report(entry, report)


def test_a_path_is_read_one_entry_per_line() -> None:
    """Scenario: a multi-line `path` is judged entry by entry, blanks dropped."""
    assert entries_of("dist/\n  logs/*.txt  \n\n") == ["dist/", "logs/*.txt"]
    assert entries_of(None) == []


def _lane_findings(path: str) -> list[str]:
    """Return the lane rule's findings when a lane step uploads `path`."""
    indented = "".join(f"            {line}\n" for line in path.splitlines())
    upload = (
        "      - uses: actions/upload-artifact@v4\n"
        f"        with:\n          path: |\n{indented}"
    )
    texts = mutate("ci.yml", LANE_STEP, upload + LANE_STEP)
    return lane_hardening_violations({"ci.yml": parse_tree(texts)["ci.yml"]})


def test_a_lane_upload_of_the_workspace_is_refused_end_to_end() -> None:
    """Scenario: the real rule refuses `.` although no line names the report."""
    found = _lane_findings(".")
    assert any("artefact" in item for item in found), found


def test_a_lane_upload_of_one_breaching_line_among_many_is_refused() -> None:
    """Scenario: a broad line hides among harmless ones in a multi-line path."""
    found = _lane_findings("dist/\n*.xml\nlogs/")
    assert any("artefact" in item for item in found), found


def test_a_lane_upload_of_harmless_lines_is_allowed_end_to_end() -> None:
    """Scenario: several unrelated entries and a negation stay allowed."""
    assert _lane_findings("dist/\nlogs/*.txt\n!dist/skip.txt") == []
