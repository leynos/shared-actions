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


def _condition(step: dict[str, object]) -> str:
    """Return a step's condition without its expression wrapper or extra spaces."""
    text = " ".join(str(step.get("if", "")).split())
    inner = text
    if text.startswith("${{"):
        inner = text.removeprefix("${{")
    if inner.endswith("}}"):
        inner = inner.removesuffix("}}")
    return inner.strip()


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


PREFLIGHT_STEP = "Validate mutation environment"
PREFLIGHT_UPLOAD_STEP = "Upload mutation preflight diagnostics"
PREFLIGHT_LANES = {"mutation-mutmut.yml": "mutmut", "mutation-cargo.yml": "cargo"}

#: The caller input naming the project's own interpreter. It must be a caller
#: input rather than the helper interpreter: the native phase judges a declared
#: support range against the interpreter the project runs under.
PREFLIGHT_PROJECT_PYTHON = {
    "mutation-mutmut.yml": "${{ inputs.python-version }}",
    "mutation-cargo.yml": "${{ inputs.project-python }}",
}

#: The exact gate each lane puts on its preflight and on its diagnostics
#: upload, as ``(preflight, upload)``. mutmut detects changes first, so it
#: validates only a diff that exists; cargo validates per shard and has no
#: change-detection step. The upload is unconditional on the preflight result
#: but still follows the caller's opt-in, because a phase envelope is what
#: explains a failed preflight.
PREFLIGHT_CONDITIONS = {
    "mutation-mutmut.yml": (
        "inputs.preflight && steps.detect.outputs.has_changes == 'true'",
        "always() && inputs.preflight && steps.detect.outputs.has_changes == 'true'",
    ),
    "mutation-cargo.yml": ("inputs.preflight", "always() && inputs.preflight"),
}


def _workflow(workflow_name: str) -> dict[str, object]:
    """Parse a workflow file and return its document."""
    document = yaml.safe_load(
        (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    )
    assert isinstance(document, dict), f"{workflow_name} must parse as a mapping"
    return typ.cast("dict[str, object]", document)


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


class TestAllowNoMutants:
    """The opt-out must reach the script, not merely exist.

    An input declared but never passed through would leave the workflow
    looking configurable while every caller got the default, which is the
    failure mode a shape test is for.
    """

    def test_the_workflow_declares_the_opt_out(self) -> None:
        """The caller needs a way to say an empty run is expected."""
        workflow = yaml.safe_load(
            (WORKFLOWS_DIR / "mutation-cargo.yml").read_text(encoding="utf-8")
        )
        triggers = workflow.get("on", workflow.get(True))
        inputs = triggers["workflow_call"]["inputs"]

        assert "allow-no-mutants" in inputs, (
            "mutation-cargo.yml must expose allow-no-mutants; without it a "
            "caller that legitimately has nothing to mutate cannot say so"
        )
        assert inputs["allow-no-mutants"].get("default") is False, (
            "allow-no-mutants must default to false, or the fail-closed "
            "behaviour is opt-in and the vacuous pass survives"
        )

    def test_the_run_step_passes_the_opt_out_to_the_script(self) -> None:
        """The environment variable the script reads must be set."""
        workflow = yaml.safe_load(
            (WORKFLOWS_DIR / "mutation-cargo.yml").read_text(encoding="utf-8")
        )
        run_steps = [
            step
            for job in workflow["jobs"].values()
            for step in _steps(job)
            if step.get("name") == "Run mutation testing"
        ]
        assert run_steps, "mutation-cargo.yml must have a 'Run mutation testing' step"

        for step in run_steps:
            env = step.get("env") or {}
            assert "INPUT_ALLOW_NO_MUTANTS" in env, (
                "the run step must pass INPUT_ALLOW_NO_MUTANTS; the script "
                "reads the opt-out from the environment and nowhere else"
            )
            assert "inputs.allow-no-mutants" in env["INPUT_ALLOW_NO_MUTANTS"], (
                "INPUT_ALLOW_NO_MUTANTS must carry the caller's input, got "
                f"{env['INPUT_ALLOW_NO_MUTANTS']!r}"
            )

    def test_the_summary_step_also_receives_the_opt_out(self) -> None:
        """The aggregate check lives in the summary job and needs it too.

        An individual empty shard is exempt, so the only place that can
        see an all-empty run is the job holding every shard at once.
        """
        workflow = yaml.safe_load(
            (WORKFLOWS_DIR / "mutation-cargo.yml").read_text(encoding="utf-8")
        )
        summary_steps = [
            step
            for job in workflow["jobs"].values()
            for step in _steps(job)
            if step.get("name") == "Post summary"
        ]
        assert summary_steps, "mutation-cargo.yml must have a 'Post summary' step"

        for step in summary_steps:
            env = step.get("env") or {}
            assert "INPUT_ALLOW_NO_MUTANTS" in env, (
                "the summary step must pass INPUT_ALLOW_NO_MUTANTS, or the "
                "aggregate check cannot honour the caller's opt-out"
            )
            assert "inputs.allow-no-mutants" in env["INPUT_ALLOW_NO_MUTANTS"], (
                "the summary step must pass the caller's input rather than a "
                f"literal, got {env['INPUT_ALLOW_NO_MUTANTS']!r}; a hard-coded "
                f"false would fail an empty aggregate run the caller allowed"
            )


class TestPreflightWiring:
    """The environment preflight must run, and run before the work it guards.

    The helper is only worth having if the workflow actually calls it, with the
    lane it belongs to, from the relocated workflow checkout, and before the
    step that enumerates mutants. An input declared but never passed through
    would leave every caller on the default, which is the failure this class
    exists to catch.
    """

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_the_workflow_declares_the_opt_in(self, workflow_name: str) -> None:
        """A caller opts in explicitly; the check is not imposed unannounced."""
        workflow = _workflow(workflow_name)
        triggers = typ.cast("dict[str, object]", workflow.get("on", workflow.get(True)))
        inputs = typ.cast(
            "dict[str, object]",
            typ.cast("dict[str, object]", triggers["workflow_call"])["inputs"],
        )

        assert "preflight" in inputs, (
            f"{workflow_name} must expose preflight; without it no caller can "
            f"validate its mutation environment"
        )
        assert (
            typ.cast("dict[str, object]", inputs["preflight"]).get("default") is False
        ), (
            "preflight must default to false, or every existing caller starts "
            "running a check it has not declared requirements for"
        )

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_the_preflight_runs_before_the_mutation_run(
        self, workflow_name: str
    ) -> None:
        """Every job that mutates also validates first, and names its lane."""
        jobs = _jobs(workflow_name)
        mutation_jobs = [
            job_name
            for job_name, job in jobs.items()
            if "Run mutation testing" in _step_names(_steps(job))
        ]
        assert mutation_jobs, f"{workflow_name} never runs mutation testing"
        for job_name in mutation_jobs:
            steps = _steps(jobs[job_name])
            names = _step_names(steps)
            assert PREFLIGHT_STEP in names, (
                f"{workflow_name}:{job_name} mutates without validating the "
                f"environment first; one wired job must not mask this one"
            )
            assert names.index(PREFLIGHT_STEP) < names.index("Run mutation testing"), (
                f"{workflow_name}:{job_name} must validate before enumerating "
                f"mutants, or the failure it exists to prevent has already "
                f"happened"
            )
            step = steps[names.index(PREFLIGHT_STEP)]
            env = typ.cast("dict[str, object]", step.get("env") or {})
            assert env.get("INPUT_LANE") == PREFLIGHT_LANES[workflow_name], (
                f"{workflow_name}:{job_name} must declare the lane it belongs "
                f"to, got {env.get('INPUT_LANE')!r}"
            )
            assert env.get("WORKFLOW_DIR") == RELOCATED_DIR_EXPR, (
                f"{workflow_name}:{job_name} must read the helper from the "
                f"relocated workflow checkout"
            )
            wanted_python = PREFLIGHT_PROJECT_PYTHON[workflow_name]
            assert env.get("INPUT_PROJECT_PYTHON") == wanted_python, (
                f"{workflow_name}:{job_name} must pass the caller's project "
                f"interpreter ({wanted_python}), got "
                f"{env.get('INPUT_PROJECT_PYTHON')!r}; the native phase cannot "
                f"judge a support range without it"
            )
            assert "mutation_preflight.py" in str(step.get("run")), (
                f"{workflow_name}:{job_name} preflight does not invoke the helper"
            )
            expected, _ = PREFLIGHT_CONDITIONS[workflow_name]
            assert _condition(step) == expected, (
                f"{workflow_name}:{job_name} preflight condition must be "
                f"{expected!r}, got {_condition(step)!r}; a negated opt-in or a "
                f"step that can never run would leave the check unenforced"
            )

    @pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
    def test_a_failed_preflight_still_publishes_its_diagnostics(
        self, workflow_name: str
    ) -> None:
        """The phase envelope is the artefact that explains a failed preflight."""
        _, expected = PREFLIGHT_CONDITIONS[workflow_name]
        guarded = [
            (job_name, _steps(job))
            for job_name, job in _jobs(workflow_name).items()
            if PREFLIGHT_STEP in _step_names(_steps(job))
        ]
        assert guarded, f"{workflow_name} never runs the environment preflight"
        for job_name, steps in guarded:
            uploads = [
                step for step in steps if step.get("name") == PREFLIGHT_UPLOAD_STEP
            ]
            assert uploads, (
                f"{workflow_name}:{job_name} must upload its own preflight "
                f"diagnostics; an upload in another job cannot explain this "
                f"job's failed preflight"
            )
            for step in uploads:
                condition = _condition(step)
                assert condition == expected, (
                    f"{workflow_name}:{job_name} preflight upload condition must "
                    f"be {expected!r}, got {condition!r}; the envelope must "
                    f"survive a failed preflight and stay off when the caller "
                    f"did not opt in"
                )
