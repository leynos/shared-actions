"""Scope the lane's artefact-upload rule to the runner that holds the report.

`coverage.lane-hardening` refuses an `upload-artifact` step whose `path` could
select the lane's coverage report. A report sits in one job's workspace, so
only a step on that runner (the lane job's own steps, and the local actions
that job runs) can select it. An upload in another job, in a workflow with no
coverage lane, or in an action no lane job runs cannot, however broad its path.

The real-workflow cases are trimmed copies of chutoro, cuprum and mriya
workflows, recorded in `tests/real_workflows`, whose benchmark, proptest and
release uploads the rule refused although no lane could reach them.
"""

from __future__ import annotations

import pathlib

import pytest
from contract_fixtures import PULL_REQUEST_LANE, REPOSITORY
from cv005_contracts.actions import load_action
from cv005_contracts.hardening import lane_hardening_violations
from cv005_contracts.loading import Document, load_workflow

REAL = pathlib.Path(__file__).parent / "real_workflows"
LANE_STEP = "      - name: Test and Measure Coverage\n"
BROAD_UPLOAD = (
    "      - uses: actions/upload-artifact@v4\n"
    "        with:\n"
    "          name: logs\n"
    "          path: .\n"
)
BUILD_ACTION = ".github/actions/build"
OTHER_ACTION = ".github/actions/other"


def _real(name: str) -> Document:
    """Return one recorded real workflow, or local action, parsed."""
    text = (REAL / name).read_text("utf-8")
    return (load_action if name.endswith("-action.yml") else load_workflow)(text)


def _action(steps: str) -> Document:
    """Return a local composite action holding `steps`."""
    return load_action("name: Example\nruns:\n  using: composite\n  steps:\n" + steps)


def _findings(closure: dict[str, Document]) -> list[str]:
    """Return the lane rule's findings over a closure."""
    return lane_hardening_violations(closure, None, REPOSITORY)


def _lane(extra_step: str = "") -> Document:
    """Return the fixture lane with `extra_step` inserted before its coverage step."""
    return load_workflow(PULL_REQUEST_LANE.replace(LANE_STEP, extra_step + LANE_STEP))


def _calling(action: str) -> str:
    """Return a step that runs a local action."""
    return f"      - uses: ./{action}\n"


@pytest.mark.parametrize(
    "name",
    [
        "mriya-release.yml",
        "chutoro-property-tests.yml",
        "chutoro-benchmark-regressions.yml",
    ],
)
def test_real_uploads_in_a_workflow_with_no_lane_are_allowed(name: str) -> None:
    """Scenario: release, proptest and benchmark uploads beside a clean lane."""
    closure = {"ci.yml": _lane(), name: _real(name)}
    assert _findings(closure) == [], name


def test_real_uploads_in_other_jobs_and_unrun_actions_are_allowed() -> None:
    """Scenario: cuprum's lane job is clean while its other jobs upload."""
    closure = {
        "ci.yml": _real("cuprum-ci.yml"),
        ".github/actions/build-wheels": _real("cuprum-build-wheels-action.yml"),
        ".github/actions/pure-python-wheel": _real(
            "cuprum-pure-python-wheel-action.yml"
        ),
    }
    assert _findings(closure) == []


def test_an_upload_in_another_job_of_the_lane_workflow_is_allowed() -> None:
    """Scenario: a second job on its own runner uploads the whole workspace."""
    second = "  logs:\n    runs-on: ubuntu-latest\n    steps:\n" + BROAD_UPLOAD
    document = load_workflow(PULL_REQUEST_LANE + second)
    assert _findings({"ci.yml": document}) == []


def test_an_upload_in_the_lane_job_is_still_refused() -> None:
    """Scenario: the lane's own job publishes the workspace, report included."""
    found = _findings({"ci.yml": _lane(BROAD_UPLOAD)})
    assert any("artefact" in item for item in found), found


def test_an_upload_in_an_action_the_lane_job_runs_is_refused() -> None:
    """Scenario: the lane job calls a local action that uploads the workspace."""
    closure = {
        "ci.yml": _lane(_calling(BUILD_ACTION)),
        BUILD_ACTION: _action(
            "    - uses: actions/upload-artifact@v4\n      with:\n        path: .\n"
        ),
    }
    found = _findings(closure)
    assert any(item.startswith(f"{BUILD_ACTION}:") for item in found), found


def test_an_upload_two_actions_down_from_the_lane_job_is_refused() -> None:
    """Scenario: the lane job runs an action that runs the uploading action."""
    closure = {
        "ci.yml": _lane(_calling(BUILD_ACTION)),
        BUILD_ACTION: _action(f"    - uses: ./{OTHER_ACTION}\n      shell: bash\n"),
        OTHER_ACTION: _action(
            "    - uses: actions/upload-artifact@v4\n      with:\n        path: .\n"
        ),
    }
    found = _findings(closure)
    assert any(item.startswith(f"{OTHER_ACTION}:") for item in found), found


def test_an_uploading_action_no_lane_job_runs_is_allowed() -> None:
    """Scenario: an action in the closure that the lane job never calls."""
    closure = {
        "ci.yml": _lane(),
        OTHER_ACTION: _action(
            "    - uses: actions/upload-artifact@v4\n      with:\n        path: .\n"
        ),
    }
    assert _findings(closure) == []
