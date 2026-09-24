"""Contract for the job ceiling: which tier a job sits in, and its minutes.

A job without `timeout-minutes` inherits GitHub's six-hour default,
which is not a budget anybody chose; it is the absence of one. Every
lane this repository owns carries a ceiling from a named tier, and the
tiers are sized from measured run history rather than from a round
number that looked safe. "Runner placement and job ceilings" in
`docs/developers-guide.md` records the measurements.

This module is exhaustive by construction: every job in the repository
belongs to exactly one of `JOB_TIERS`, `CONSUMER_OWNED_JOBS` or
`reading.CALLER_JOBS`, so a new workflow added without a decision about
its ceiling fails here rather than inheriting the six-hour default
quietly. It is kept apart from `test_runner_placement.py` because the
ceiling and the runner label are independent decisions about the same
job, sized from different evidence.
"""

from __future__ import annotations

import typing as typ

import pytest

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Job ceilings by tier, in minutes, with the measurement each was sized
#: against recorded in the developers' guide.
TIMEOUT_TIERS: typ.Final[cabc.Mapping[str, int]] = {
    # Checkout, run an action, assert its outputs. No download of a
    # release archive, no compilation. Longest observed: 13 s.
    "assertion": 10,
    # Downloads and verifies a pinned release archive. Longest observed:
    # 5 min 40 s, on the Windows Whitaker leg.
    "install": 15,
    # Cross-compiles the toy application. Longest observed: 4 min 36 s,
    # on a Windows leg.
    "build": 20,
    # Runs the Python suite uninstrumented. Longest observed: 12 min 45 s.
    "suite": 20,
    # Runs the same suite under the coverage action. Longest observed:
    # 6 min 53 s.
    "coverage": 30,
}

#: Every job this repository owns, mapped to its tier. Exhaustive by
#: construction: `TestJobClassification` fails when a job appears that
#: is in neither this mapping nor `CONSUMER_OWNED_JOBS` nor
#: `reading.CALLER_JOBS`.
JOB_TIERS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("ci.yml", "python-tests"): "suite",
    ("ci.yml", "python-tests-windows"): "suite",
    ("ci.yml", "coverage"): "coverage",
    ("coverage-main.yml", "coverage-upload"): "coverage",
    ("rust-toy-app.yml", "build-release"): "build",
    ("test-determine-release-modes.yml", "test-determine-modes"): "assertion",
    ("test-export-cargo-metadata.yml", "test-export-metadata"): "assertion",
    (
        "test-export-cargo-metadata.yml",
        "test-export-metadata-env-overrides",
    ): "assertion",
    (
        "test-export-ubicloud-cache-credentials.yml",
        "refuses-a-github-hosted-runner",
    ): "assertion",
    ("test-codescene-parser-proof.yml", "parser-proof"): "install",
    ("test-install-mdtablefix.yml", "install-mdtablefix"): "install",
    ("test-install-mdtablefix.yml", "install-mdtablefix-no-prebuilt"): "install",
    ("test-install-tool.yml", "installs-and-caches"): "install",
    ("test-install-tool.yml", "refuses-an-unpinned-version"): "assertion",
    ("test-install-tool.yml", "refuses-an-unsupported-target"): "assertion",
    ("test-install-whitaker.yml", "install-whitaker"): "install",
    ("test-install-whitaker.yml", "install-whitaker-windows"): "install",
    ("test-install-whitaker.yml", "install-whitaker-failure"): "assertion",
    ("test-resolve-workflow-source.yml", "resolve"): "assertion",
    (
        "test-rust-build-release-root-discovery.yml",
        "test-action-setup-root",
    ): "assertion",
    ("test-rustflags-export.yml", "rust-build-release-exports"): "assertion",
    (
        "test-rustflags-export.yml",
        "rust-build-release-defers-to-inherited",
    ): "assertion",
    ("test-rustflags-export.yml", "setup-rust-exports"): "assertion",
    ("test-rustflags-export.yml", "setup-rust-with-inherited"): "assertion",
    ("test-rustflags-export.yml", "setup-rust-toolchain-available"): "assertion",
    ("test-setup-rust-sccache.yml", "exports-the-wrapper"): "assertion",
    (
        "test-upload-codescene-coverage.yml",
        "cold-runner-contract",
    ): "install",
    ("test-setup-rust-sccache.yml", "respects_a_caller_backend_choice"): "assertion",
    ("test-setup-rust-sccache.yml", "respects_a_caller_wrapper"): "assertion",
    (
        "test-setup-rust-sccache.yml",
        "restores_a_caller_cache_service_choice",
    ): "assertion",
    ("test-stage-release-artefacts.yml", "test-stage-artefacts"): "assertion",
    (
        "test-stage-release-artefacts.yml",
        "test-stage-artefacts-env-overrides",
    ): "assertion",
    (
        "test-stage-release-artefacts.yml",
        "test-stage-artefacts-powershell-help",
    ): "assertion",
    ("test-stage-release-artefacts.yml", "test-stage-artefacts-binstall"): "assertion",
    ("test-ubicloud-sccache-proxy.yml", "reaches-the-proxy"): "assertion",
    ("test-upload-release-assets.yml", "test-upload-assets-dry-run"): "assertion",
    ("test-upload-release-assets.yml", "test-upload-assets-env-overrides"): "assertion",
}

#: Jobs in reusable workflows that other repositories call. Their run
#: history lives in those repositories, so this repository cannot size a
#: ceiling for them from evidence and does not pretend to. They are
#: outside the ceiling contract; their placement is still governed by
#: `reading.HOSTED_LINUX_EXEMPTIONS`.
CONSUMER_OWNED_JOBS: typ.Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("mutation-cargo.yml", "detect"),
        ("mutation-cargo.yml", "mutants"),
        ("mutation-cargo.yml", "summarize"),
        ("mutation-mutmut.yml", "mutants"),
        ("dependabot-automerge.yml", "automerge"),
    }
)


class TestJobClassification:
    """Every job belongs to exactly one of the three ceiling partitions."""

    @pytest.mark.parametrize(
        ("workflow", "job_id"),
        reading.all_jobs(),
        ids=lambda value: value if isinstance(value, str) else str(value),
    )
    def test_every_job_is_classified(self, workflow: str, job_id: str) -> None:
        """Every job belongs to exactly one of the three partitions.

        A new workflow added without a decision about its runner and its
        ceiling fails here rather than inheriting both defaults quietly.
        """
        pair = (workflow, job_id)
        memberships = [
            pair in JOB_TIERS,
            pair in CONSUMER_OWNED_JOBS,
            pair in reading.CALLER_JOBS,
        ]
        assert sum(memberships) == 1, (
            f"{reading.identifier(workflow, job_id)} must appear in exactly "
            "one of JOB_TIERS, CONSUMER_OWNED_JOBS or reading.CALLER_JOBS; it "
            f"appears in {sum(memberships)}"
        )


class TestJobCeilingTiers:
    """Each owned job carries the ceiling its tier specifies."""

    @pytest.mark.parametrize(
        ("workflow", "job_id"), sorted(JOB_TIERS), ids=reading.identifier
    )
    def test_a_job_carries_the_ceiling_of_its_tier(
        self, workflow: str, job_id: str
    ) -> None:
        """Each owned job declares the `timeout-minutes` its tier specifies.

        Asserting the exact value rather than "some ceiling is present"
        keeps the guide's table and the workflows from drifting apart, and
        makes raising a ceiling a decision with a measurement behind it.
        """
        jobs = dict(reading.jobs(workflow))
        identifier = reading.identifier(workflow, job_id)
        assert job_id in jobs, f"{identifier} no longer exists"
        tier = JOB_TIERS[(workflow, job_id)]
        expected = TIMEOUT_TIERS[tier]
        declared = jobs[job_id].get("timeout-minutes")
        assert declared == expected, (
            f"{identifier} is in the {tier!r} tier and must "
            f"declare timeout-minutes: {expected}; it declares {declared!r}"
        )


class TestUnownedJobsExist:
    """A job excused from the ceiling contract must still be real."""

    @pytest.mark.parametrize(
        ("workflow", "job_id"),
        sorted(reading.CALLER_JOBS | CONSUMER_OWNED_JOBS),
        ids=reading.identifier,
    )
    def test_an_unowned_job_still_exists(self, workflow: str, job_id: str) -> None:
        """A job excused from the ceiling contract must still be real.

        The two exclusion sets are the only way out of the ceiling rule, so
        a stale entry in either is a hole in it.
        """
        assert job_id in dict(reading.jobs(workflow)), (
            f"{reading.identifier(workflow, job_id)} is excused from the "
            "ceiling contract but no such job exists; delete the entry"
        )
