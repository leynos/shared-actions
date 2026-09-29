"""Refusal cases for the publisher's report wiring and conditions.

The upload must read the report its own job wrote earlier, in the format
written, and nothing may skip the publisher's work on a push. Each case
changes one thing in the compliant fixture tree and asserts on the one
rule that must refuse it.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PUBLISHER, mutate
from cv005_contracts.loading import Document, load_workflow
from cv005_contracts.publisher import COVERAGE_ACTION
from cv005_contracts.publisher_rules import (
    condition_violations,
)
from cv005_contracts.wiring import _merged_into, wiring_violations


def _publisher(texts: dict[str, str]) -> Document:
    """Return the parsed publisher of a tree."""
    return load_workflow(texts["coverage-main.yml"])


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("      path: coverage.xml\n", "      path: other.xml\n"),
        ("      mode: upload\n", "      mode: upload\n          format: lcov\n"),
    ],
)
def test_the_upload_reads_what_the_publisher_writes(old: str, new: str) -> None:
    """An upload reading another file or format sends nothing useful."""
    texts = mutate("coverage-main.yml", old, new)
    found = wiring_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize("old", ["no such text", "coverage.xml"])
def test_a_mutation_changes_exactly_one_place(old: str) -> None:
    """A case changing two places could pass on the one it does not name."""
    with pytest.raises(ValueError, match="exactly one thing"):
        mutate("coverage-main.yml", old, "other.xml")


UPLOAD_PATH = "          path: coverage.xml\n"
CHECK_NAME = "      - name: Check for the CodeScene token\n"
WRITTEN = "          output-path: coverage.xml\n"


@pytest.mark.parametrize(
    ("replacements"),
    [
        # The uploader's `__auto__` path is coverage.xml for cobertura.
        [(UPLOAD_PATH, "")],
        [(UPLOAD_PATH, "          path: __auto__\n")],
        # lading's shape: the generator's format left to its default.
        [(UPLOAD_PATH, "          path: coverage.xml\n          format: cobertura\n")],
        # For lcov the uploader's default path is lcov.info.
        [
            (UPLOAD_PATH, "          format: lcov\n"),
            (WRITTEN, "          output-path: lcov.info\n          format: lcov\n"),
        ],
    ],
)
def test_the_actions_defaults_are_read(replacements: list[tuple[str, str]]) -> None:
    """An omitted input reads as what the action does by default."""
    text = PUBLISHER
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    found = wiring_violations(load_workflow(text))
    assert not found, found


@pytest.mark.parametrize("written", ["other.xml", "lcov.info"])
def test_a_default_path_must_match_what_was_written(written: str) -> None:
    """The resolved default is compared, not taken as a match."""
    text = PUBLISHER.replace(UPLOAD_PATH, "").replace(
        WRITTEN, f"          output-path: {written}\n"
    )
    found = wiring_violations(load_workflow(text))
    assert found, found


#: The fixture's upload input, pointed at a merged report.
MERGED_PATH = "          path: merged.xml\n"


GENERATOR_NAME = "      - name: Generate coverage\n"


def _merged(merge_run: str, anchor: str = CHECK_NAME) -> str:
    """Return the publisher uploading merged.xml from a merge step before `anchor`."""
    merge = f"      - name: Merge coverage results\n        run: {merge_run}\n"
    text = PUBLISHER.replace(anchor, merge + anchor).replace(UPLOAD_PATH, MERGED_PATH)
    assert MERGED_PATH in text
    assert merge in text
    return text


def test_a_merged_report_is_read() -> None:
    """The mxd shape: legs merged into the uploaded file by a redirect."""
    found = wiring_violations(load_workflow(_merged("merge 'cov-*.xml' > merged.xml")))
    assert not found, found


@pytest.mark.parametrize(
    ("merge_run", "anchor"),
    [
        ("merge 'cov-*.xml' > merged.xml", GENERATOR_NAME),
        ("merge 'cov-*.xml' > other.xml", CHECK_NAME),
        ("cat merged.xml", CHECK_NAME),
    ],
)
def test_a_merge_must_follow_a_leg_and_write_the_file(
    merge_run: str, anchor: str
) -> None:
    """A merge before any leg, or not redirected to the file, writes nothing."""
    text = _merged(merge_run, anchor)
    found = wiring_violations(load_workflow(text))
    assert found, found


def test_an_unnamed_report_is_refused() -> None:
    """A generator naming no file writes nothing the uploader's default reads."""
    text = PUBLISHER.replace("          path: coverage.xml\n", "").replace(
        "          output-path: coverage.xml\n", ""
    )
    assert text.count("coverage.xml") == 0, text
    found = wiring_violations(load_workflow(text))
    assert found, found


#: The publisher fixture's coverage step, for the cases that move it.
GENERATOR = PUBLISHER[
    PUBLISHER.index("      - name: Generate coverage\n") : PUBLISHER.index(
        "      - name: Check for the CodeScene token\n"
    )
]


def test_the_report_is_written_before_the_upload() -> None:
    """A generator after the upload leaves the uploader nothing to read."""
    text = PUBLISHER.replace(GENERATOR, "") + GENERATOR
    found = wiring_violations(load_workflow(text))
    assert found, found


def test_a_report_from_another_job_is_refused() -> None:
    """The uploader reads its own job's workspace, not another job's."""
    other = "  measure:\n    runs-on: ubuntu-latest\n    steps:\n" + GENERATOR
    text = PUBLISHER.replace(GENERATOR, "").replace("jobs:\n", "jobs:\n" + other)
    found = wiring_violations(load_workflow(text))
    assert found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "    runs-on: ubuntu-latest\n",
            (
                "    runs-on: ubuntu-latest\n"
                "    if: github.event_name == 'workflow_dispatch'\n"
            ),
        ),
        (
            "      - name: Generate coverage\n",
            (
                "      - name: Generate coverage\n"
                "        if: github.event_name == 'workflow_dispatch'\n"
            ),
        ),
    ],
)
def test_nothing_can_skip_the_publisher_on_a_push(old: str, new: str) -> None:
    """A job or coverage-step condition could skip the baseline on a push."""
    texts = mutate("coverage-main.yml", old, new)
    found = condition_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize(
    ("anchor", "addition"),
    [
        ("    runs-on: ubuntu-latest\n", "    continue-on-error: true\n"),
        ("      - name: Generate coverage\n", "        continue-on-error: true\n"),
        (
            "      - name: Upload coverage data to CodeScene\n",
            "        continue-on-error: true\n",
        ),
    ],
)
def test_nothing_in_the_publisher_may_fail_quietly(anchor: str, addition: str) -> None:
    """`continue-on-error` would leave a failed baseline or upload green."""
    texts = mutate("coverage-main.yml", anchor, anchor + addition)
    found = condition_violations(_publisher(texts))
    assert any("continue-on-error" in item for item in found), found


@pytest.mark.parametrize(
    "extra", ["            if: false\n", "            continue-on-error: true\n"]
)
def test_a_merge_that_may_be_skipped_or_fail_green_is_not_a_merge(extra: str) -> None:
    """The upload would read a stale or missing report, and the clause must say so."""
    generator: dict[str, object] = {
        "uses": f"{COVERAGE_ACTION}@x",
        "with": {"format": "lcov"},
    }
    merge: dict[str, object] = {"run": "merge 'lcov-*.info' > lcov.info"}
    merge |= {"continue-on-error": True} if "continue" in extra else {"if": "false"}
    assert _merged_into([generator, merge], ("lcov.info", "lcov")) is False


def test_an_unconditional_merge_still_counts() -> None:
    """The narrow case: the same merge with no condition is accepted."""
    generator: dict[str, object] = {
        "uses": f"{COVERAGE_ACTION}@x",
        "with": {"format": "lcov"},
    }
    merge: dict[str, object] = {"run": "merge 'lcov-*.info' > lcov.info"}
    assert _merged_into([generator, merge], ("lcov.info", "lcov")) is True
