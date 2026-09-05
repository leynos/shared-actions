"""Shape tests for the mutation-testing reusable workflows.

The workflows check out their own source into ``workflow-src/`` inside
the caller's workspace. Left there, the checkout pollutes the tree
under test: callers with tree-scanning hygiene tests (manifest sweeps,
file inventories, lint-everything globs) fail their unmutated baseline
on every real run (issue #343). These tests parse both workflows with
PyYAML and pin the corrective invariant: every job that checks out the
workflow repository must relocate it to ``$RUNNER_TEMP`` before any
step consumes it, and every ``WORKFLOW_DIR`` consumer must read the
relocated path.

The relocation has a second edge. GitHub re-reads a local action's
``action.yml`` when it runs that action's post step, so a job that both
relocates the checkout and loads an action out of it must put the tree
back before the post phase, or the job fails during cleanup with its
real work already done. That is pinned here too.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW_NAMES = ("mutation-cargo.yml", "mutation-mutmut.yml")

CHECKOUT_STEP = "Checkout workflow repository"
RELOCATE_STEP = "Relocate workflow source"
RESTORE_STEP = "Restore workflow source"

#: Where the workflow repository is checked out inside the caller's workspace.
CHECKOUT_PATH = "./workflow-src"
RELOCATED_DIR_EXPR = "${{ steps.relocate-workflow-source.outputs.workflow_dir }}"

pytestmark = pytest.mark.skipif(
    not all((WORKFLOWS_DIR / name).exists() for name in WORKFLOW_NAMES),
    reason="workflow files not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)


def _jobs(workflow_name: str) -> dict[str, dict[str, object]]:
    """Parse a workflow file and return its jobs mapping."""
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    )
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), f"{workflow_name} must declare a jobs mapping"
    return typ.cast("dict[str, dict[str, object]]", jobs)


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return a job's step list."""
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare a steps list"
    return typ.cast("list[dict[str, object]]", steps)


def _step_names(steps: list[dict[str, object]]) -> list[object]:
    """Return the step names in order."""
    return [step.get("name") for step in steps]


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_every_workflow_checkout_is_followed_by_relocation(
    workflow_name: str,
) -> None:
    """Each job checking out workflow-src relocates it out of the workspace."""
    for job_name, job in _jobs(workflow_name).items():
        steps = _steps(job)
        names = _step_names(steps)
        if CHECKOUT_STEP not in names:
            continue
        assert RELOCATE_STEP in names, (
            f"{workflow_name}:{job_name} checks out the workflow repository "
            f"but never relocates it; the checkout pollutes the caller's "
            f"tree during mutation runs (issue #343)"
        )
        assert names.index(RELOCATE_STEP) > names.index(CHECKOUT_STEP), (
            f"{workflow_name}:{job_name} must relocate workflow-src after "
            f"checking it out"
        )
        relocate = steps[names.index(RELOCATE_STEP)]
        run = relocate.get("run")
        assert isinstance(run, str), (
            f"{workflow_name}:{job_name} relocation step must have a run block"
        )
        assert '"${RUNNER_TEMP}/workflow-src"' in run, (
            f"{workflow_name}:{job_name} relocation must move workflow-src "
            f"to $RUNNER_TEMP, outside the caller's workspace"
        )


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_workflow_dir_consumers_read_the_relocated_path(
    workflow_name: str,
) -> None:
    """Steps after relocation take WORKFLOW_DIR from the relocation output."""
    for job_name, job in _jobs(workflow_name).items():
        steps = _steps(job)
        names = _step_names(steps)
        if RELOCATE_STEP not in names:
            continue
        relocate_index = names.index(RELOCATE_STEP)
        consumers = [
            (step.get("name"), typ.cast("dict[str, object]", step.get("env", {})))
            for step in steps[relocate_index + 1 :]
            if isinstance(step.get("env"), dict) and "WORKFLOW_DIR" in step["env"]
        ]
        assert consumers, (
            f"{workflow_name}:{job_name} relocates workflow-src but no later "
            f"step consumes WORKFLOW_DIR; the relocation step is vestigial"
        )
        for step_name, env in consumers:
            assert env["WORKFLOW_DIR"] == RELOCATED_DIR_EXPR, (
                f"{workflow_name}:{job_name} step {step_name!r} must take "
                f"WORKFLOW_DIR from the relocation step's output, got "
                f"{env['WORKFLOW_DIR']!r}"
            )


def _workspace_local_action_steps(
    steps: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return the steps loading an action from the workflow checkout."""
    # Only these make the relocation a cleanup hazard: GitHub re-reads a local
    # action's action.yml to run its post step, and a remote action's code does
    # not live in the moved tree. The checkout root counts as well as its
    # descendants, since `uses: ./workflow-src` is valid when that directory
    # holds an action.yml.
    return [
        step
        for step in steps
        if (uses := str(step.get("uses") or "")) == CHECKOUT_PATH
        or uses.startswith(f"{CHECKOUT_PATH}/")
    ]


def _restore_condition(job: dict[str, object]) -> str | None:
    """Return a job's restore condition, or ``None`` when it has no restore."""
    steps = _steps(job)
    names = _step_names(steps)
    if RESTORE_STEP not in names:
        return None
    return " ".join(str(steps[names.index(RESTORE_STEP)].get("if", "")).split())


class TestRestoreWorkflowSource:
    """The relocation must not outlive the job that depends on the checkout."""

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_a_relocating_job_that_uses_a_local_action_restores_the_checkout(
        self, workflow_name: str
    ) -> None:
        """A job cannot move a local action out from under its own post step.

        Post steps run after the last regular step, so the restore has to be a
        regular step and it has to be the last one that touches the tree. Without
        it the job fails at ``Post Setup Rust`` having already produced its
        mutation results, which reads as a mutation failure and is not one.
        """
        for job_name, job in _jobs(workflow_name).items():
            steps = _steps(job)
            names = _step_names(steps)
            if RELOCATE_STEP not in names:
                continue
            local_actions = _workspace_local_action_steps(steps)
            if not local_actions:
                continue

            assert RESTORE_STEP in names, (
                f"{workflow_name}:{job_name} relocates workflow-src and loads "
                f"{[s.get('name') for s in local_actions]} from it, so it must "
                f"restore the checkout before its post steps run"
            )
            assert names.index(RESTORE_STEP) > names.index(RELOCATE_STEP), (
                f"{workflow_name}:{job_name} restores workflow-src before it "
                f"relocates it"
            )
            assert names.index(RESTORE_STEP) == len(names) - 1, (
                f"{workflow_name}:{job_name} must restore workflow-src in its "
                f"last step; a later step would see the checkout the relocation "
                f"exists to hide"
            )

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_the_restore_runs_even_when_the_mutation_run_failed(
        self, workflow_name: str
    ) -> None:
        """Cleanup must not be conditional on the work having succeeded.

        Without ``always()`` a failed mutation run would leave the checkout
        outside the workspace, and the job would report the post-step failure on
        top of the real one.
        """
        for job_name, job in _jobs(workflow_name).items():
            condition = _restore_condition(job)
            if condition is None:
                continue

            assert "always()" in condition, (
                f"{workflow_name}:{job_name} restore is not guarded by always(): "
                f"{condition!r}"
            )

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_the_restore_runs_when_the_job_is_cancelled(
        self, workflow_name: str
    ) -> None:
        """A cancelled job is the ending that most needs the restore.

        ``always()`` runs on cancellation, which is why GitHub cautions that it
        can keep a cancelled job alive; the restore is one ``mv``, so that
        caution does not apply here. ``!cancelled()`` is the idiom that would
        exclude this ending, and it must not appear: a job cancelled by its
        ceiling or by hand would otherwise leave the checkout displaced, with
        the post phase still to run and no later step to notice.
        """
        for job_name, job in _jobs(workflow_name).items():
            condition = _restore_condition(job)
            if condition is None:
                continue

            assert "cancelled()" not in condition, (
                f"{workflow_name}:{job_name} restore excludes the cancelled "
                f"path: {condition!r}"
            )
