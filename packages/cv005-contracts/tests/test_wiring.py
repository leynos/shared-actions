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
from cv005_contracts.publisher_rules import (
    condition_violations,
    wiring_violations,
)


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


def test_an_unnamed_report_is_refused() -> None:
    """Two absent inputs compare equal, so both ends must name the file."""
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
