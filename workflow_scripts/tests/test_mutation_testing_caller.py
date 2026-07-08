"""Contract tests for the mutation-testing caller workflow.

The executable logic lives in this repository's own reusable workflow,
``.github/workflows/mutation-mutmut.yml``, which carries its own unit
and integration tests; the caller
(``.github/workflows/mutation-testing-caller.yml``) is declarative
configuration pinned by SHA like any external consumer. These tests
parse the caller with PyYAML and pin the contract it must uphold, so
drift (repointing the pin at a branch, widening permissions, or losing
the flat-layout configuration) fails CI on the pull request rather than
surfacing in a scheduled or manual run.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "mutation-testing-caller.yml"
)

pytestmark = pytest.mark.skipif(
    not WORKFLOW_PATH.exists(),
    reason="workflow file not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)

#: The shared-actions commit the caller pins. Bump the workflow and this
#: constant together.
PINNED_SHA = "859416a90eb3987b46a57682c5d6b8964ad3f0a6"

EXPECTED_USES = (
    "leynos/shared-actions/.github/workflows/mutation-mutmut.yml@" + PINNED_SHA
)


def _load() -> dict[str, object]:
    """Parse the workflow file."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    """Return the ``on:`` mapping (PyYAML parses the bare key as True)."""
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "the workflow must declare an on: mapping"
    return typ.cast("dict[str, object]", triggers)


def _mutation_job(workflow: dict[str, object]) -> dict[str, object]:
    """Return the single calling job."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "the workflow must declare a jobs mapping"
    assert jobs, "the workflow must declare at least one job"
    assert list(jobs) == ["mutation"], (
        f"expected a single job named 'mutation', found {sorted(jobs)}"
    )
    job = typ.cast("dict[str, object]", jobs)["mutation"]
    assert isinstance(job, dict), "jobs.mutation must be a mapping"
    return typ.cast("dict[str, object]", job)


def test_uses_reference_is_pinned_to_the_documented_sha() -> None:
    """The job must call the reusable workflow at the exact documented SHA."""
    uses = _mutation_job(_load()).get("uses")
    assert isinstance(uses, str), "jobs.mutation.uses is missing"
    path, _, ref = uses.partition("@")
    assert path == "leynos/shared-actions/.github/workflows/mutation-mutmut.yml", (
        f"jobs.mutation.uses must reference mutation-mutmut.yml, got {path!r}"
    )
    assert len(ref) == 40, (
        f"jobs.mutation.uses must pin a full 40-character commit SHA, "
        f"not a branch or tag: {ref!r}"
    )
    assert all(c in "0123456789abcdef" for c in ref), (
        f"jobs.mutation.uses must pin a lowercase hex commit SHA, "
        f"not a branch or tag: {ref!r}"
    )
    assert uses == EXPECTED_USES, (
        f"jobs.mutation.uses pins {ref!r}; this test documents {PINNED_SHA!r} — "
        "bump the workflow and this test together"
    )


def test_job_permissions_are_exactly_least_privilege() -> None:
    """The job grants contents: read and id-token: write, nothing broader."""
    permissions = _mutation_job(_load()).get("permissions")
    assert permissions == {"contents": "read", "id-token": "write"}, (
        "jobs.mutation.permissions must be exactly "
        f"{{'contents': 'read', 'id-token': 'write'}}, got {permissions!r}"
    )


def test_workflow_default_permissions_are_empty() -> None:
    """The workflow-level default token scope is empty."""
    workflow = _load()
    assert workflow.get("permissions") == {}, (
        f"top-level permissions must be an empty mapping, got "
        f"{workflow.get('permissions')!r}"
    )


def test_concurrency_serializes_per_ref_without_cancelling() -> None:
    """Runs queue per ref instead of cancelling one another."""
    concurrency = _load().get("concurrency")
    assert isinstance(concurrency, dict), "the workflow must declare concurrency"
    assert concurrency.get("group") == "mutation-testing-${{ github.ref }}", (
        f"concurrency.group must key on the triggering ref, got "
        f"{concurrency.get('group')!r}"
    )
    assert concurrency.get("cancel-in-progress") is False, (
        f"concurrency.cancel-in-progress must be false, got "
        f"{concurrency.get('cancel-in-progress')!r}"
    )


def test_triggers_keep_schedule_and_plain_dispatch() -> None:
    """The daily schedule stays; dispatch declares no inputs."""
    triggers = _triggers(_load())
    schedule = triggers.get("schedule")
    assert schedule == [{"cron": "50 11 * * *"}], (
        f"on.schedule must be the daily 11:50 UTC cron, got {schedule!r}"
    )
    assert "workflow_dispatch" in triggers, "on.workflow_dispatch is missing"
    dispatch = triggers.get("workflow_dispatch") or {}
    assert isinstance(dispatch, dict)
    assert not dispatch.get("inputs"), (
        "on.workflow_dispatch must not declare inputs; the Actions "
        "run-workflow control selects the ref"
    )


def test_with_block_carries_the_caller_configuration() -> None:
    """The caller sets the flat-layout paths and prefix strip, nothing else."""
    with_block = _mutation_job(_load()).get("with")
    assert with_block == {
        "paths": "workflow_scripts/",
        "module-prefix-strip": "",
    }, (
        "jobs.mutation.with must set exactly paths 'workflow_scripts/' "
        "(the mutable source lives there, not under src/) and "
        "module-prefix-strip '' (flat layout: changed-file paths already "
        f"start at the package directory), got {with_block!r}"
    )
