"""Contract for ``cargo binstall`` carrying a token in ``mutation-cargo.yml``.

``cargo binstall`` resolves releases through ``api.github.com``. Anonymous
requests share a per-runner-IP rate limit, so an unlucky run receives a 403,
waits 120 seconds and then compiles the crate from source: 348 seconds on one
consumer leg. A warm cache hides the failure, which is why it needs a contract
rather than a run. The reusable workflow is inherited by every mutation
consumer, so the step is asserted here once.

Two halves, so that removing either cannot hide behind the other: at least one
step must invoke ``cargo binstall`` (a guard over a filtered list passes when
the list is empty), and every such step must receive ``GITHUB_TOKEN`` from the
workflow token rather than from a literal or another variable.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mutation-cargo.yml"
)
#: A `cargo binstall <crate>` invocation, not a mention or a version probe.
BINSTALL = re.compile(
    r"(?:^|[;&|(]|\bthen\b|\bdo\b)\s*cargo\s+binstall\s+(?!-V\b|--version\b)\S",
    re.MULTILINE,
)
TOKEN_EXPRESSIONS = ("${{ github.token }}", "${{ secrets.GITHUB_TOKEN }}")

pytestmark = pytest.mark.skipif(
    not WORKFLOW.exists(),
    reason="workflow file not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)


def _binstall_steps() -> list[tuple[str, dict[str, typ.Any], dict[str, typ.Any]]]:
    """Return (job id, job, step) for every step that runs ``cargo binstall``."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return [
        (job_id, job, step)
        for job_id, job in jobs.items()
        for step in job.get("steps", [])
        if BINSTALL.search(step.get("run") or "")
    ]


def test_the_workflow_still_runs_cargo_binstall() -> None:
    """The token assertion below has something to judge.

    Without this, deleting the install step would leave the token contract
    green over an empty set.
    """
    assert _binstall_steps(), "mutation-cargo.yml must run cargo binstall"


def test_every_binstall_step_carries_the_workflow_token() -> None:
    """Each ``cargo binstall`` step gets GITHUB_TOKEN from the workflow token."""
    for job_id, job, step in _binstall_steps():
        token = {**(job.get("env") or {}), **(step.get("env") or {})}.get(
            "GITHUB_TOKEN"
        )
        assert token in TOKEN_EXPRESSIONS, (
            f"{job_id}: step {step.get('name')!r} runs cargo binstall with "
            f"GITHUB_TOKEN={token!r}; it must be one of {TOKEN_EXPRESSIONS}, or "
            "an unlucky run waits 120 s on a 403 and compiles from source"
        )
