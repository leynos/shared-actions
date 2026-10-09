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
from cv005_contracts.hardening import (
    _persistent_dependents,
    lane_hardening_violations,
)
from cv005_contracts.loading import Document, WorkflowReadingError, load_workflow
from cv005_contracts.reading import jobs

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


def _upload(path: str) -> str:
    """Return an `upload-artifact` step with `path`."""
    return (
        "      - uses: actions/upload-artifact@v4\n"
        f"        with:\n          path: {path}\n"
    )


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
    found = _findings(closure)
    assert found == [], found


def test_an_upload_in_another_job_of_the_lane_workflow_is_allowed() -> None:
    """Scenario: a second job on its own runner uploads the whole workspace."""
    second = "  logs:\n    runs-on: ubuntu-latest\n    steps:\n" + BROAD_UPLOAD
    document = load_workflow(PULL_REQUEST_LANE + second)
    found = _findings({"ci.yml": document})
    assert found == [], found


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
    found = _findings(closure)
    assert found == [], found


@pytest.mark.parametrize(
    "path",
    [
        "${{ runner.temp }}/sccache-publish.json",
        "${{ runner.temp }}/benchmark-gate/decisions.jsonl",
        "${{ runner.temp }}/benchmark-ratchet/**/*.json",
    ],
)
def test_a_lane_upload_from_the_runner_temp_directory_is_allowed(path: str) -> None:
    """Scenario: the lane job uploads files the runner wrote outside the workspace.

    rstest-bdd and cuprum upload files under `runner.temp`, which GitHub
    places beside the workspace, not in it.
    """
    upload = _upload(path)
    found = _findings({"ci.yml": _lane(upload)})
    assert found == [], found


@pytest.mark.parametrize(
    "path",
    [
        "${{ runner.temp }}/../work/repo/coverage.xml",
        "${{ runner.temp }}/${{ matrix.os }}",
        "${{ runner.tmp }}/x",
        "${{ github.workspace }}/coverage.xml",
    ],
)
def test_a_runner_temp_lookalike_that_could_reach_the_workspace_is_refused(
    path: str,
) -> None:
    """Scenario: a climb, a further expression or another root is not scratch."""
    upload = _upload(path)
    found = _findings({"ci.yml": _lane(upload)})
    assert any("artefact" in item for item in found), found


JOB_HEADER = "    runs-on: ubuntu-latest\n    permissions:"
LANE_GUARD = "        if: github.event_name == 'pull_request'\n"


def _with_job_condition(condition: str, *, keep_step_guard: bool = True) -> Document:
    """Return the fixture lane with a job-level `if:` and, optionally, no step guard."""
    text = PULL_REQUEST_LANE.replace(
        JOB_HEADER, f"    if: {condition}\n{JOB_HEADER}", 1
    )
    if not keep_step_guard:
        text = text.replace(LANE_GUARD, "", 1)
    return load_workflow(text)


def test_a_lane_job_that_excludes_another_event_is_allowed() -> None:
    """Scenario: netsuke's build job skips scheduled runs and still serves the lane.

    An event cannot be a pull request and a schedule at once, so the exclusion
    changes nothing for the lane whose step carries the pull-request guard.
    """
    document = _with_job_condition("github.event_name != 'schedule'")
    found = _findings({"ci.yml": document})
    assert found == [], found


@pytest.mark.parametrize(
    "condition",
    [
        "github.event_name != 'pull_request'",
        "github.ref == 'refs/heads/main'",
        "github.event_name != 'schedule' && github.ref == 'refs/heads/main'",
        "github.event_name != 'schedule' || true",
    ],
)
def test_a_lane_job_condition_that_could_switch_the_lane_off_is_refused(
    condition: str,
) -> None:
    """Scenario: excluding pull requests, or any other term, still skips the lane."""
    found = _findings({"ci.yml": _with_job_condition(condition)})
    assert any("pull-request guard" in item for item in found), found


def test_a_job_exclusion_needs_the_step_to_carry_the_pull_request_guard() -> None:
    """Scenario: without the step's guard the exclusion no longer implies anything."""
    document = _with_job_condition(
        "github.event_name != 'schedule'", keep_step_guard=False
    )
    found = _findings({"ci.yml": document})
    assert any("pull-request guard" in item for item in found), found


def _later_job(name: str, runs_on: str, needs: str | None) -> str:
    """Return a second job uploading the whole workspace."""
    waits = f"    needs: {needs}\n" if needs else ""
    return f"  {name}:\n    runs-on: {runs_on}\n{waits}    steps:\n" + BROAD_UPLOAD


@pytest.mark.parametrize(
    ("runs_on", "needs"),
    [
        ("self-hosted", "build-test"),
        ("[self-hosted, linux]", "build-test"),
        ("self-hosted", "[build-test]"),
    ],
)
def test_a_later_job_on_a_self_hosted_runner_may_share_the_workspace(
    runs_on: str, needs: str
) -> None:
    """Scenario: a self-hosted runner need not clean up between jobs.

    GitHub makes no promise of a fresh instance there, so a job that waits for
    the lane and uploads the workspace can publish the lane's report.
    """
    text = PULL_REQUEST_LANE + _later_job("publish-logs", runs_on, needs)
    found = _findings({"ci.yml": load_workflow(text)})
    assert any("artefact" in item for item in found), found


def test_a_job_two_needs_after_the_lane_on_a_self_hosted_runner_is_read() -> None:
    """Scenario: the wait is transitive, through an intermediate hosted job."""
    middle = "  middle:\n    runs-on: ubuntu-latest\n    needs: build-test\n"
    middle += "    steps:\n      - run: true\n"
    text = (
        PULL_REQUEST_LANE + middle + _later_job("publish-logs", "self-hosted", "middle")
    )
    found = _findings({"ci.yml": load_workflow(text)})
    assert any("artefact" in item for item in found), found


@pytest.mark.parametrize(
    ("runs_on", "needs"),
    [
        ("ubuntu-latest", "build-test"),
        ("ubicloud-standard-2", "build-test"),
        ("self-hosted", None),
    ],
)
def test_a_later_job_on_a_fresh_runner_or_not_waiting_is_allowed(
    runs_on: str, needs: str | None
) -> None:
    """Scenario: hosted runners start clean; a job not waiting for the lane is apart."""
    text = PULL_REQUEST_LANE + _later_job("publish-logs", runs_on, needs)
    found = _findings({"ci.yml": load_workflow(text)})
    assert found == [], found


def test_a_lane_step_naming_this_repositorys_action_at_a_ref_is_refused() -> None:
    """Scenario: the repository lets the rule refuse a call it cannot follow.

    `owner/repo/.github/actions/x@main` runs a revision this checkout does not
    hold. Without the repository the call would read as a remote action and the
    upload inside it would go unjudged, so the rule refuses to read the lane.
    """
    call = f"      - uses: {REPOSITORY}/.github/actions/build@main\n"
    with pytest.raises(WorkflowReadingError):
        _findings({"ci.yml": _lane(call)})


def test_a_later_self_hosted_job_running_an_uploading_action_is_refused() -> None:
    """Scenario: the later job reaches the retained report through a local action."""
    later = (
        "  publish-logs:\n    runs-on: self-hosted\n    needs: build-test\n"
        f"    steps:\n{_calling(BUILD_ACTION)}"
    )
    closure = {
        "ci.yml": load_workflow(PULL_REQUEST_LANE + later),
        BUILD_ACTION: _action(
            "    - uses: actions/upload-artifact@v4\n      with:\n        path: .\n"
        ),
    }
    found = _findings(closure)
    assert any(item.startswith(f"{BUILD_ACTION}:") for item in found), found


NAMES: tuple[str, ...] = ("a", "b", "c")
PAIRS: tuple[tuple[int, int], ...] = tuple(
    (i, j) for i in range(3) for j in range(3) if i != j
)


def _reachable(edges: frozenset[tuple[int, int]], lane: int) -> set[int]:
    """Return the jobs reachable from `lane` along `needs` edges, by search."""
    seen = {lane}
    pending = [lane]
    while pending:
        node = pending.pop()
        for before, after in edges:
            if before == node and after not in seen:
                seen.add(after)
                pending.append(after)
    return seen - {lane}


def _graph(edges: frozenset[tuple[int, int]], hosted: int) -> Document:
    """Return a three-job workflow: edge `(a, b)` makes `b` wait for `a`."""
    jobs_text = ""
    for index, name in enumerate(NAMES):
        waits = [NAMES[i] for i, j in sorted(edges) if j == index]
        runner = "self-hosted" if hosted >> index & 1 else "ubuntu-latest"
        needs = f"    needs: [{', '.join(waits)}]\n" if waits else ""
        jobs_text += (
            f"  {name}:\n    runs-on: {runner}\n{needs}    steps:\n      - run: true\n"
        )
    return load_workflow("on: pull_request\njobs:\n" + jobs_text)


def _dependents(document: Document, lane: int) -> set[str]:
    """Return the identifiers `_persistent_dependents` reads for one lane job."""
    found = _persistent_dependents(document, jobs(document)[NAMES[lane]])
    return {ident for ident, _ in found}


def test_the_self_hosted_dependents_match_a_reachability_oracle() -> None:
    """Property: over every `needs` graph of three jobs, the dependents are exact.

    For every edge subset, every choice of which jobs are self-hosted and each
    job as the lane, the jobs read are the self-hosted ones reachable from the
    lane, by an independent graph search.
    """
    for mask in range(1 << len(PAIRS)):
        edges = frozenset(p for bit, p in enumerate(PAIRS) if mask >> bit & 1)
        for hosted in range(8):
            document = _graph(edges, hosted)
            for lane in range(3):
                want = {NAMES[j] for j in _reachable(edges, lane) if hosted >> j & 1}
                got = _dependents(document, lane)
                assert got == want, (edges, hosted, lane, got, want)
