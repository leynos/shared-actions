"""Contract-test the repository's main-owned CodeScene coverage flow."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
CI_PATH: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
MAIN_PATH: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "workflows" / "coverage-main.yml"
)


def _load_workflow(path: Path) -> dict[typ.Any, typ.Any]:
    """Load the workflow mapping at ``path``."""
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path.name} must contain a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", workflow)


def _workflow_job(
    workflow: dict[typ.Any, typ.Any], name: str
) -> dict[typ.Any, typ.Any]:
    """Return the named job from a workflow mapping."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"jobs.{name} must be a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", job)


def _workflow_step(job: dict[typ.Any, typ.Any], name: str) -> dict[typ.Any, typ.Any]:
    """Return the uniquely named step from a workflow job."""
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare a steps list"
    matching_steps = [
        step for step in steps if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matching_steps) == 1, (
        f"expected one step named {name!r}, got {len(matching_steps)}"
    )
    return typ.cast("dict[typ.Any, typ.Any]", matching_steps[0])


def test_pull_request_coverage_is_local_and_ratcheted() -> None:
    """Keep pull-request coverage serial, scoped, and independent of CodeScene."""
    coverage_job = _workflow_job(_load_workflow(CI_PATH), "coverage")
    checkout = _workflow_step(coverage_job, "Checkout repository")
    coverage = _workflow_step(coverage_job, "Generate coverage")

    assert "fetch-depth" not in checkout.get("with", {})
    assert coverage["with"] == {
        "language": "python",
        "python-source": "workflow_scripts",
        "output-path": "coverage.xml",
        "format": "cobertura",
        "use-cargo-nextest": "false",
        "pytest-workers": "",
        "with-ratchet": "true",
    }
    assert "CS_ACCESS_TOKEN" not in coverage_job.get("env", {})
    assert not any(
        isinstance(step, dict)
        and "upload-codescene-coverage" in str(step.get("uses", ""))
        for step in coverage_job["steps"]
    )


def test_main_push_owns_codescene_upload() -> None:
    """Upload the same ratcheted coverage only from the main workflow."""
    workflow = _load_workflow(MAIN_PATH)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {
        "push": {"branches": ["main"]},
        "workflow_dispatch": None,
    }
    coverage_job = _workflow_job(workflow, "coverage-upload")
    coverage = _workflow_step(coverage_job, "Generate coverage")
    upload = _workflow_step(coverage_job, "Upload coverage to CodeScene")

    assert coverage["with"] == {
        "language": "python",
        "python-source": "workflow_scripts",
        "output-path": "coverage.xml",
        "format": "cobertura",
        "use-cargo-nextest": "false",
        "pytest-workers": "",
        "with-ratchet": "true",
    }
    assert upload["uses"] == "./.github/actions/upload-codescene-coverage"
    assert upload["with"]["mode"] == "upload"
