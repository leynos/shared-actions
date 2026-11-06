"""Contract for mutmut failure diagnostics and unchanged result uploads."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from .workflow_yaml import load_workflow

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "mutation-mutmut.yml"
)
DIAGNOSTICS_DIR = "mutation-mutmut-diagnostics"
DIAGNOSTIC_OUTCOME_EXPRESSION = (
    """contains(fromJSON('["failure","cancelled"]'), steps.mutation.outcome)"""
)


def _mapping(value: object, *, name: str) -> cabc.Mapping[str, object]:
    """Narrow one parsed workflow value to a mapping at a named boundary."""
    assert isinstance(value, cabc.Mapping), f"{name} must be a mapping"
    return typ.cast("cabc.Mapping[str, object]", value)


def _steps() -> list[cabc.Mapping[str, object]]:
    """Return the mutmut job steps with YAML syntax already validated."""
    workflow = _mapping(load_workflow(WORKFLOW), name="workflow")
    jobs = _mapping(workflow["jobs"], name="jobs")
    job = _mapping(jobs["mutants"], name="mutants job")
    steps = job["steps"]
    assert isinstance(steps, list), "mutants job steps must be a list"
    return [_mapping(step, name="mutants job step") for step in steps]


def _step(
    steps: list[cabc.Mapping[str, object]], name: str
) -> cabc.Mapping[str, object]:
    """Return the uniquely named workflow step."""
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def _text(step: cabc.Mapping[str, object], key: str) -> str:
    """Return a workflow step field after narrowing it to text."""
    value = step[key]
    assert isinstance(value, str), f"step field {key!r} must be a string"
    return value


@pytest.fixture
def workflow_steps() -> list[cabc.Mapping[str, object]]:
    """Load parsed mutmut workflow steps for one contract test."""
    return _steps()


def test_mutation_step_runs_through_the_bounded_capture_wrapper(
    workflow_steps: list[cabc.Mapping[str, object]],
) -> None:
    """The wrapper surrounds uv startup and the complete mutmut baseline."""
    mutation = _step(workflow_steps, "Run mutation testing")

    assert _text(mutation, "id") == "mutation", (
        "Expected mutation step runs through the bounded capture wrapper."
    )
    assert mutation["timeout-minutes"] == "${{ fromJSON(inputs.timeout-minutes) }}", (
        "Expected mutation step runs through the bounded capture wrapper."
    )
    run = _text(mutation, "run")
    assert "mutation_capture.py" in run, (
        "Expected mutation step runs through the bounded capture wrapper."
    )
    assert f"--output-dir {DIAGNOSTICS_DIR}" in run, (
        "Expected mutation step runs through the bounded capture wrapper."
    )
    assert "uv run --script" in run, (
        "Expected mutation step runs through the bounded capture wrapper."
    )


def test_failed_mutation_requires_and_uploads_diagnostics_without_results(
    workflow_steps: list[cabc.Mapping[str, object]],
) -> None:
    """Failure diagnostics do not depend on either results artefact."""
    verify = _step(workflow_steps, "Verify mutation failure diagnostics")
    diagnostic_upload = _step(workflow_steps, "Upload mutation failure diagnostics")
    diagnostic_upload_inputs = _mapping(
        diagnostic_upload["with"], name="diagnostic upload inputs"
    )

    verify_if = _text(verify, "if")
    upload_if = _text(diagnostic_upload, "if")
    verify_run = _text(verify, "run")
    step_names = [step.get("name") for step in workflow_steps]

    assert DIAGNOSTIC_OUTCOME_EXPRESSION in verify_if, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert f"--output-dir {DIAGNOSTICS_DIR}" in verify_run, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert "--verify-failure" in verify_run, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert "always()" in verify_if, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert DIAGNOSTIC_OUTCOME_EXPRESSION in upload_if, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert "always()" in upload_if, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert (
        step_names.index("Run mutation testing")
        < step_names.index("Verify mutation failure diagnostics")
        < step_names.index("Upload mutation failure diagnostics")
    ), "Expected failed mutation requires and uploads diagnostics without results."
    diagnostic_path = _text(diagnostic_upload_inputs, "path")
    assert diagnostic_path == f"{DIAGNOSTICS_DIR}/", (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert _text(diagnostic_upload_inputs, "if-no-files-found") == "error", (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert "mutation-mutmut-results.txt" not in diagnostic_path, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )
    assert "mutmut-stats.json" not in diagnostic_path, (
        "Expected failed mutation requires and uploads diagnostics without results."
    )


def test_existing_result_upload_and_workflow_limits_are_preserved(
    workflow_steps: list[cabc.Mapping[str, object]],
) -> None:
    """Result/statistics paths, runner, permissions and timeout remain stable."""
    workflow = _mapping(load_workflow(WORKFLOW), name="workflow")
    jobs = _mapping(workflow["jobs"], name="jobs")
    job = _mapping(jobs["mutants"], name="mutants job")
    permissions = _mapping(job["permissions"], name="mutants job permissions")
    result_upload = _step(workflow_steps, "Upload mutation report")
    result_upload_inputs = _mapping(
        result_upload["with"], name="mutation report upload inputs"
    )

    assert _text(job, "runs-on") == "ubuntu-latest", (
        "Expected existing result upload and workflow limits are preserved."
    )
    assert job["timeout-minutes"] == 360, (
        "Expected existing result upload and workflow limits are preserved."
    )
    assert permissions == {"contents": "read", "id-token": "write"}, (
        "Expected existing result upload and workflow limits are preserved."
    )
    assert _text(result_upload, "if") == (
        "${{ always() && steps.detect.outputs.has_changes == 'true' }}"
    ), "Expected existing result upload and workflow limits are preserved."
    assert _text(result_upload_inputs, "path").splitlines() == [
        "mutation-mutmut-results.txt",
        "mutants/mutmut-stats.json",
    ], "Expected existing result upload and workflow limits are preserved."
    assert _text(result_upload_inputs, "if-no-files-found") == "warn", (
        "Expected existing result upload and workflow limits are preserved."
    )


def test_no_change_skip_does_not_require_failure_diagnostics(
    workflow_steps: list[cabc.Mapping[str, object]],
) -> None:
    """Intentional no-change runs remain distinct from failed execution."""
    mutation = _step(workflow_steps, "Run mutation testing")
    diagnostic_upload = _step(workflow_steps, "Upload mutation failure diagnostics")

    assert (
        _text(mutation, "if") == "${{ steps.detect.outputs.has_changes == 'true' }}"
    ), "Expected no change skip does not require failure diagnostics."
    diagnostic_if = _text(diagnostic_upload, "if")
    assert DIAGNOSTIC_OUTCOME_EXPRESSION in diagnostic_if, (
        "Expected no change skip does not require failure diagnostics."
    )
