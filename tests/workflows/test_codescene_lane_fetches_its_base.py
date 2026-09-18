"""Contract that the CodeScene cold-runner lane fetches the ref it compares against.

`cs-coverage check` computes a merge base against the pull request's base
branch. The lane fetches that branch itself, because the checkout is
shallow and a ref nobody fetched is not a revision.

The two are only the same branch on a pull request against the default
branch. On a stacked pull request the base is another branch, and a lane
fetching the default branch instead failed with `ambiguous argument
'origin/<base>': unknown revision`, on a step whose name said it had
fetched the merge base. That is asserted here rather than waited for,
because the lane is green on every unstacked pull request and the defect
appears only where a stack does.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOW: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "workflows" / "test-upload-codescene-coverage.yml"
)

#: The expression the fetch must resolve its branch from. `github.base_ref`
#: is the pull request's base and is empty on every other event, so the
#: fallback is what a dispatch gets. Written out rather than matched
#: loosely: `github.ref_name` is the head on a pull request and would send
#: the step to fetch the branch it is already on.
BASE_REF_EXPRESSION: typ.Final[str] = "github.base_ref"

#: The expression that may stand beside it, and only as the fallback.
DEFAULT_BRANCH_EXPRESSION: typ.Final[str] = "github.event.repository.default_branch"


def _steps() -> cabc.Iterator[tuple[str, dict[str, typ.Any]]]:
    """Yield each step in the workflow, with the job id that declares it."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"{WORKFLOW} is not a mapping"
        raise TypeError(msg)
    for job_id, job in (document.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                yield str(job_id), step


def _fetching_steps() -> list[tuple[str, dict[str, typ.Any]]]:
    """Return every step whose script fetches a branch into `origin/`."""
    return [
        (job_id, step)
        for job_id, step in _steps()
        if "git fetch" in str(step.get("run", ""))
    ]


def test_the_lane_fetches_a_branch_at_all() -> None:
    """Some step fetches, so the rule below has a subject.

    Without this, deleting the fetch would satisfy every assertion by
    leaving nothing to check, and the merge base would go missing again.
    """
    assert _fetching_steps(), (
        f"no step in {WORKFLOW.name} runs `git fetch`; the shallow checkout "
        "then has no base branch for cs-coverage to compare against"
    )


@pytest.mark.parametrize(
    ("job_id", "step"), _fetching_steps(), ids=lambda value: str(value)[:40]
)
def test_the_fetch_resolves_the_pull_requests_base(
    job_id: str, step: dict[str, typ.Any]
) -> None:
    """The branch fetched comes from the pull request's base, not the default.

    Both halves matter. Without `github.base_ref` a stacked pull request
    fetches the wrong branch and the merge base is missing. Without the
    fallback a dispatch, which carries no base ref, fetches nothing and
    fails the same way from the other direction.
    """
    environment = step.get("env") or {}
    sources = " ".join(str(value) for value in environment.values())

    assert BASE_REF_EXPRESSION in sources, (
        f"{job_id}'s fetch step resolves its branch from {sources!r}, which "
        f"does not read {BASE_REF_EXPRESSION}; a pull request stacked on "
        "another branch then fetches the default branch and cs-coverage "
        "fails on a revision nobody fetched"
    )
    assert DEFAULT_BRANCH_EXPRESSION in sources, (
        f"{job_id}'s fetch step has no fallback to "
        f"{DEFAULT_BRANCH_EXPRESSION}; a workflow_dispatch run carries no "
        "base ref and would fetch an empty branch name"
    )


@pytest.mark.parametrize(
    ("job_id", "step"), _fetching_steps(), ids=lambda value: str(value)[:40]
)
def test_the_fetch_uses_the_branch_it_resolved(
    job_id: str, step: dict[str, typ.Any]
) -> None:
    """Every ref the script names is the resolved branch, not a literal.

    Resolving the branch into a variable and then fetching a hard-coded
    one would satisfy the rule above while changing nothing, so the
    script is read as well as its environment.
    """
    script = str(step.get("run", ""))
    names = [
        name
        for name, value in (step.get("env") or {}).items()
        if BASE_REF_EXPRESSION in str(value)
    ]

    assert names, f"{job_id} resolves no branch variable to read"
    assert all(f"${name}" in script for name in names), (
        f"{job_id} resolves {names} but its script never reads them: "
        "the fetch would name some other branch"
    )
    assert "default_branch" not in script, (
        f"{job_id}'s script names the default branch directly; the branch "
        "to fetch is whatever the environment resolved"
    )
