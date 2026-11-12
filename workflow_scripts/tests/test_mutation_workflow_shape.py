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

Setup Rust is referenced remotely by full SHA, so its cache-save post hook
remains under the runner-managed ``_actions`` directory when the checkout is
relocated. The shape contract also prohibits steps from referencing actions
through the moved ``./workflow-src/`` path.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW_NAMES = ("mutation-cargo.yml", "mutation-mutmut.yml")

CHECKOUT_STEP = "Checkout workflow repository"
RELOCATE_STEP = "Relocate workflow source"
POST_HOOK_SMOKE_WORKFLOW = "test-mutation-cargo-post-hook.yml"

#: Where the workflow repository is checked out inside the caller's workspace.
CHECKOUT_PATH = "./workflow-src"
RELOCATED_DIR_EXPR = "${{ steps.relocate-workflow-source.outputs.workflow_dir }}"
LOCAL_WORKFLOW_SRC_PREFIX = "./workflow-src/"
REMOTE_SETUP_RUST_PREFIX = "leynos/shared-actions/.github/actions/setup-rust@"

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
def test_no_step_references_a_relocated_workflow_src_action(
    workflow_name: str,
) -> None:
    """No step invokes a composite action via the ``./workflow-src/`` path.

    A workspace-relative ``uses: ./workflow-src/...`` reference breaks at
    job teardown: the Actions runtime re-resolves the local action's
    ``action.yml`` from that path when running its post hook, but the
    "Relocate workflow source" step has by then moved ``workflow-src`` to
    ``$RUNNER_TEMP``. The post step fails to find the definition and reddens
    an otherwise green run (issue #365). Composite actions with post hooks
    must instead be referenced remotely (pinned by SHA); the runner
    materialises those under its managed ``_actions`` directory, outside the
    caller's workspace and surviving teardown.
    """
    for job_name, job in _jobs(workflow_name).items():
        for step in _steps(job):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            assert not uses.startswith(LOCAL_WORKFLOW_SRC_PREFIX), (
                f"{workflow_name}:{job_name} step {step.get('name')!r} "
                f"references {uses!r}; a workspace-relative workflow-src "
                f"action path breaks its post hook once workflow-src is "
                f"relocated to $RUNNER_TEMP (issue #365). Reference the "
                f"action remotely, pinned by SHA, instead."
            )
    return


def test_mutants_setup_rust_survives_workflow_source_relocation() -> None:
    """Setup Rust is remote, pinned by full SHA, and skipped under act."""
    steps = _steps(_jobs("mutation-cargo.yml")["mutants"])
    names = _step_names(steps)
    assert "Setup Rust" in names, "mutants job must contain a Setup Rust step"
    assert RELOCATE_STEP in names, "mutants job must relocate workflow-src"
    setup_rust = steps[names.index("Setup Rust")]
    uses = setup_rust.get("uses")

    assert isinstance(uses, str)
    assert re.fullmatch(
        rf"{re.escape(REMOTE_SETUP_RUST_PREFIX)}[0-9a-f]{{40}}", uses
    ), "mutants:Setup Rust must use the remote action pinned by a full SHA"
    assert setup_rust.get("if") == "${{ env.ACT != 'true' }}"

    relocate = steps[names.index(RELOCATE_STEP)]
    run = relocate.get("run")
    assert isinstance(run, str)
    assert '"${RUNNER_TEMP}/workflow-src"' in run


def test_hosted_post_hook_smoke_uses_the_same_setup_rust_pin() -> None:
    """The hosted teardown smoke test exercises the production action pin."""
    mutants_steps = _steps(_jobs("mutation-cargo.yml")["mutants"])
    mutants_setup = next(
        step for step in mutants_steps if step.get("name") == "Setup Rust"
    )
    smoke_steps = _steps(_jobs(POST_HOOK_SMOKE_WORKFLOW)["post-hook"])
    smoke_setup = next(step for step in smoke_steps if step.get("name") == "Setup Rust")
    smoke_names = _step_names(smoke_steps)

    uses = mutants_setup.get("uses")
    assert isinstance(uses, str)
    assert re.fullmatch(rf"{re.escape(REMOTE_SETUP_RUST_PREFIX)}[0-9a-f]{{40}}", uses)
    assert smoke_setup.get("uses") == uses
    assert RELOCATE_STEP in smoke_names
    assert smoke_names.index("Setup Rust") < smoke_names.index(RELOCATE_STEP)

    relocate = smoke_steps[smoke_names.index(RELOCATE_STEP)]
    run = relocate.get("run")
    assert isinstance(run, str)
    assert '"${GITHUB_WORKSPACE}/workflow-src"' in run
    assert '"${RUNNER_TEMP}/workflow-src"' in run


def test_hosted_post_hook_smoke_enables_cache_save_post_steps() -> None:
    """The smoke workflow must register Setup Rust's cache-save post hook."""
    steps = _steps(_jobs(POST_HOOK_SMOKE_WORKFLOW)["post-hook"])
    setup_rust = next(step for step in steps if step.get("name") == "Setup Rust")
    inputs = setup_rust.get("with")

    assert isinstance(inputs, dict)
    assert inputs.get("cache-provider") == "github"
    assert inputs.get("save-cache") == "true"


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
