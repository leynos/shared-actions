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


def test_cargo_mutants_install_is_locked() -> None:
    """The cargo-mutants install pins its dependency resolution with --locked.

    ``cargo binstall`` falls back to a source build when no prebuilt binary
    matches the runner. Without ``--locked`` that fallback resolves the
    dependency tree to the newest permitted versions and can pull in a
    transitive crate whose MSRV exceeds the pinned nightly toolchain, failing
    the install before any mutant runs (issue #364).
    """
    steps = [
        step
        for job in _jobs("mutation-cargo.yml").values()
        for step in _steps(job)
        if step.get("name") == "Install cargo-mutants"
    ]
    assert steps, "mutation-cargo.yml must install cargo-mutants"
    for step in steps:
        run = step.get("run")
        assert isinstance(run, str), "install step must have a run block"
        binstall_lines = [line for line in run.splitlines() if "cargo binstall" in line]
        assert binstall_lines, "install step must invoke cargo binstall"
        for line in binstall_lines:
            assert "--locked" in line, (
                f"cargo binstall must pass --locked so the source-build "
                f"fallback honours the committed Cargo.lock (issue #364): "
                f"{line.strip()!r}"
            )


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
