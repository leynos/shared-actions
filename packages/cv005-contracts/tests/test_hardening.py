"""Refusal cases for the publisher's and the lanes' least-privilege rules.

Each case changes one thing in the compliant fixture tree and asserts on the
one rule that must refuse it; the narrow cases show the rules still accept
the shapes the estate relies on.
"""

from __future__ import annotations

import pytest
from contract_fixtures import mutate, parse_tree, tree
from cv005_contracts.hardening import (
    lane_hardening_violations,
    publisher_hardening_violations,
)
from cv005_contracts.loading import load_workflow

LANE_PERMISSIONS = "    permissions:\n      contents: read\n"
LANE_STEP = "      - name: Test and Measure Coverage\n"


def _lane_findings(texts: dict[str, str]) -> list[str]:
    """Return the lane rule's findings over the fixture's pull-request lane."""
    return lane_hardening_violations({"ci.yml": parse_tree(texts)["ci.yml"]})


def test_the_compliant_tree_is_hardened() -> None:
    """The fixture publisher and lane hold every least-privilege rule."""
    documents = parse_tree(tree())
    assert publisher_hardening_violations(documents["coverage-main.yml"]) == []
    assert lane_hardening_violations({"ci.yml": documents["ci.yml"]}) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("      contents: read\n", "      contents: write\n"),
        ("    permissions:\n      contents: read\n", ""),
        ("        with:\n          persist-credentials: false\n", ""),
        (
            "          persist-credentials: false\n",
            "          persist-credentials: 'false'\n",
        ),
    ],
)
def test_the_upload_job_holds_least_privilege(old: str, new: str) -> None:
    """A writable token or a credential left by checkout is refused."""
    texts = mutate("coverage-main.yml", old, new)
    found = publisher_hardening_violations(parse_tree(texts)["coverage-main.yml"])
    assert found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (LANE_STEP, LANE_STEP + "        continue-on-error: true\n"),
        (
            "    runs-on: ubuntu-latest\n    permissions:",
            "    runs-on: ubuntu-latest\n    continue-on-error: true\n    permissions:",
        ),
        (
            "        if: github.event_name == 'pull_request'\n",
            "        if: github.event_name == 'pull_request' && github.actor != 'x'\n",
        ),
        (
            "    runs-on: ubuntu-latest\n    permissions:",
            "    runs-on: ubuntu-latest\n    if: false\n    permissions:",
        ),
        ("      contents: read\n", "      contents: write\n"),
        (LANE_PERMISSIONS, ""),
    ],
)
def test_lane_coverage_cannot_fail_green_or_write(old: str, new: str) -> None:
    """Continue-on-error, a narrowing condition or write access is refused."""
    found = _lane_findings(mutate("ci.yml", old, new))
    assert found, found


def test_workflow_level_read_only_permissions_count() -> None:
    """A lane job inheriting read-only permissions from its workflow passes."""
    texts = mutate("ci.yml", LANE_PERMISSIONS, "")
    texts["ci.yml"] = texts["ci.yml"].replace(
        "jobs:\n", "permissions:\n  contents: read\njobs:\n", 1
    )
    assert _lane_findings(texts) == []


def test_a_lane_may_not_upload_its_report_as_an_artefact() -> None:
    """`upload-artifact` would publish what `publish-artefact` keeps back."""
    upload = (
        "      - uses: actions/upload-artifact@v4\n"
        "        with:\n          path: coverage.xml\n"
    )
    found = _lane_findings(mutate("ci.yml", LANE_STEP, upload + LANE_STEP))
    assert any("artefact" in item for item in found), found


def test_an_unrelated_artefact_is_allowed() -> None:
    """Uploading something other than the report is not refused."""
    upload = (
        "      - uses: actions/upload-artifact@v4\n"
        "        with:\n          path: dist/\n"
    )
    assert _lane_findings(mutate("ci.yml", LANE_STEP, upload + LANE_STEP)) == []


def test_an_unconditional_lane_is_accepted() -> None:
    """A lane that runs only on pull requests needs no guard at all."""
    texts = mutate("ci.yml", "        if: github.event_name == 'pull_request'\n", "")
    assert _lane_findings(texts) == []


def test_a_publisher_checkout_elsewhere_is_not_the_upload_jobs() -> None:
    """Only the upload job's checkouts are held; other jobs are not publishers."""
    other = (
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
    )
    document = load_workflow(
        mutate("coverage-main.yml", "jobs:\n", "jobs:\n" + other)["coverage-main.yml"]
    )
    assert publisher_hardening_violations(document) == []
