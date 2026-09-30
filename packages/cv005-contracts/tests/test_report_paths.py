"""Refusal and narrow cases for the artefact-path guard.

A lane's `publish-artefact: 'false'` is defeated by any `upload-artifact` step
whose `path` could select the report. Each breaching entry below publishes the
report without naming it, and each narrow entry is a common upload that cannot
reach it; the pair shows the guard fires on the case and stays quiet off it.
"""

from __future__ import annotations

import itertools
import typing as typ

import pytest
from contract_fixtures import mutate, parse_tree
from cv005_contracts.expressions import yielded_operands
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
    pytest.param("[k-m]cov.info", id="a class range covering the report"),
    pytest.param("[z-a]cov.info", id="a class range that does not compile"),
    pytest.param("{.,dist}", id="a brace alternative that is the workspace"),
    pytest.param("{..,x}", id="a brace alternative that climbs out"),
    pytest.param("{/,x}", id="a brace alternative that is the root"),
    pytest.param(
        "/tmp/${{ x }}",  # noqa: S108
        id="an expression after a scratch prefix",
    ),
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
    pytest.param("[a-c]og.txt", id="a class range over other files"),
    pytest.param("{dist,logs/}", id="brace alternatives that are other directories"),
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


#: A component alphabet with no glob character, `..` or expression.
_NAMES: typ.Final[tuple[str, ...]] = ("a", "b", "cov.info")


def _reports() -> list[str]:
    """Every relative report path of one to three components over `_NAMES`."""
    return [
        "/".join(parts)
        for size in (1, 2, 3)
        for parts in itertools.product(_NAMES, repeat=size)
    ]


def test_every_directory_above_a_report_holds_it_and_no_sibling_does() -> None:
    """Property: over every report path, a prefix directory is refused, a sibling not.

    The oracle is built, not computed: each proper or whole prefix of the
    report's components names a directory (or the file) that contains it, and a
    path whose first component is not one of the alphabet's names cannot.
    """
    for report in _reports():
        parts = report.split("/")
        for size in range(1, len(parts) + 1):
            prefix = "/".join(parts[:size])
            assert could_hold_the_report(prefix, report), (prefix, report)
            assert could_hold_the_report(prefix + "/", report), (prefix, report)
        assert not could_hold_the_report("qq/", report), report


def test_a_star_at_any_depth_selects_the_report_and_a_wrong_name_does_not() -> None:
    """Property: swapping any one component for `*` still selects the report.

    Over every report path, a glob built by replacing one component with `*`
    matches it by construction, and one built by replacing that component with
    a name outside the alphabet followed by `*` matches nothing.
    """
    for report in _reports():
        parts = report.split("/")
        for index in range(len(parts)):
            starred = "/".join([*parts[:index], "*", *parts[index + 1 :]])
            assert could_hold_the_report(starred, report), (starred, report)
            wrong = "/".join([*parts[:index], "q*", *parts[index + 1 :]])
            assert not could_hold_the_report(wrong, report), (wrong, report)


def test_a_scratch_path_is_cleared_exactly_when_it_never_climbs() -> None:
    """Property: under `/tmp/`, `..` anywhere refuses and its absence clears.

    Every path of one to three segments drawn from names and `..` is built and
    the verdict compared with the presence of a `..` segment.
    """
    segments = ("a", "b", "..", ".")
    for size in (1, 2, 3):
        for chosen in itertools.product(segments, repeat=size):
            entry = "/tmp/" + "/".join(chosen)  # noqa: S108
            assert could_hold_the_report(entry, REPORT) == (".." in chosen), entry


def test_an_expression_is_cleared_only_when_every_result_is_scratch_or_empty() -> None:
    """Property: over every mix of results, one non-scratch result refuses it.

    An expression of one to three `||` alternatives, each `c && 'value'`, is
    built from scratch, empty and workspace-relative values. It is cleared
    exactly when no value is workspace-relative, and it yields those values.
    """
    values = ("'/tmp/a.log'", "''", "'dist/'")
    for size in (1, 2, 3):
        for chosen in itertools.product(values, repeat=size):
            body = " || ".join(f"c{i} && {value}" for i, value in enumerate(chosen))
            entry = "${{ " + body + " }}"
            assert yielded_operands(entry) == list(chosen), entry
            assert could_hold_the_report(entry, REPORT) == ("'dist/'" in chosen), entry
