"""Run the delayed PR comment workflow through act, as a called workflow.

These cases exercise the engine's own wiring: ``workflow_call`` inputs, the
``env:`` bindings, job permissions and step order. The comment step cannot
reach GitHub from the act container, so the accepted case proves the run got
past validation to the wait, and the rejected case proves it stopped there.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from .conftest import ActConfig, run_act, skip_unless_act, skip_unless_workflow_tests

if typ.TYPE_CHECKING:
    from pathlib import Path

WRAPPER = "test-delayed-pr-comment.yml"
JOB = "delayed-comment"


def _run(tmp_path: Path, delay: str) -> tuple[int, str]:
    """Run the wrapper under act with the given delay and return its logs."""
    event = tmp_path / "dispatch.event.json"
    inputs = {"pr_number": "12", "delay_minutes": delay, "message": "hello"}
    event.write_text(json.dumps({"inputs": inputs}), encoding="utf-8")
    config = ActConfig(artefact_dir=tmp_path / "artefacts", event_path=event)
    return run_act(WRAPPER, "workflow_dispatch", JOB, config)


@skip_unless_act
@skip_unless_workflow_tests
def test_an_accepted_delay_passes_validation_and_reaches_the_comment(
    tmp_path: Path,
) -> None:
    """A zero delay is validated, waited out and handed on to the comment step."""
    _, logs = _run(tmp_path, "0")

    assert "Success - Main Validate inputs and wait" in logs
    assert "Run Main Comment on the pull request" in logs
    assert "must be a whole number" not in logs


@skip_unless_act
@skip_unless_workflow_tests
@pytest.mark.parametrize("delay", ["346", "-1"])
def test_a_rejected_delay_stops_before_the_comment(tmp_path: Path, delay: str) -> None:
    """An out-of-range delay fails with the error and never reaches the comment."""
    code, logs = _run(tmp_path, delay)

    assert code != 0
    assert "delay_minutes must be a whole number from 0 to 345." in logs
    assert "Failure - Main Validate inputs and wait" in logs
    assert "Run Main Comment on the pull request" not in logs
