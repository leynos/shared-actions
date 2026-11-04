"""Contract for mutmut failure diagnostics and unchanged result uploads."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

from .workflow_yaml import load_workflow

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "mutation-mutmut.yml"
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


def test_mutation_step_runs_through_the_bounded_capture_wrapper() -> None:
    """The wrapper surrounds uv startup and the complete mutmut baseline."""
    steps = _steps()
    mutation = next(
        step for step in steps if step.get("name") == "Run mutation testing"
    )

    assert mutation["id"] == "mutation"
    assert mutation["timeout-minutes"] == "${{ fromJSON(inputs.timeout-minutes) }}"
    assert "mutation_capture.py" in mutation["run"]
    assert "--output-dir mutation-mutmut-diagnostics" in mutation["run"]
    assert "uv run --script" in mutation["run"]


def test_failed_mutation_requires_and_uploads_diagnostics_without_results() -> None:
    """Failure diagnostics do not depend on either results artefact."""
    steps = _steps()
    verify = next(
        step
        for step in steps
        if step.get("name") == "Verify mutation failure diagnostics"
    )
    diagnostic_upload = next(
        step
        for step in steps
        if step.get("name") == "Upload mutation failure diagnostics"
    )

    assert "always()" in verify["if"]
    assert "steps.mutation.outcome == 'failure'" in verify["if"]
    assert "--verify-failure" in verify["run"]
    assert "always()" in diagnostic_upload["if"]
    assert "steps.mutation.outcome == 'failure'" in diagnostic_upload["if"]
    assert diagnostic_upload["with"]["path"] == "mutation-mutmut-diagnostics/"
    assert diagnostic_upload["with"]["if-no-files-found"] == "error"
    assert "mutation-mutmut-results.txt" not in diagnostic_upload["with"]["path"]
    assert "mutmut-stats.json" not in diagnostic_upload["with"]["path"]


def test_existing_result_upload_and_workflow_limits_are_preserved() -> None:
    """Result/statistics paths, runner, permissions and timeout remain stable."""
    steps = _steps()
    workflow = _mapping(load_workflow(WORKFLOW), name="workflow")
    jobs = _mapping(workflow["jobs"], name="jobs")
    job = _mapping(jobs["mutants"], name="mutants job")
    permissions = _mapping(job["permissions"], name="mutants job permissions")
    result_upload = next(
        step for step in steps if step.get("name") == "Upload mutation report"
    )

    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 360
    assert permissions == {"contents": "read", "id-token": "write"}
    assert result_upload["if"] == (
        "${{ always() && steps.detect.outputs.has_changes == 'true' }}"
    )
    assert result_upload["with"]["path"].splitlines() == [
        "mutation-mutmut-results.txt",
        "mutants/mutmut-stats.json",
    ]
    assert result_upload["with"]["if-no-files-found"] == "warn"


def test_no_change_skip_does_not_require_failure_diagnostics() -> None:
    """Intentional no-change runs remain distinct from failed execution."""
    steps = _steps()
    mutation = next(
        step for step in steps if step.get("name") == "Run mutation testing"
    )
    diagnostic_upload = next(
        step
        for step in steps
        if step.get("name") == "Upload mutation failure diagnostics"
    )

    assert mutation["if"] == "${{ steps.detect.outputs.has_changes == 'true' }}"
    assert "steps.mutation.outcome == 'failure'" in diagnostic_upload["if"]
