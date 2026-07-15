"""Integration tests for the mutation-testing reusable workflows.

These tests exercise the change-detection guard end-to-end under act:
the wrapper workflows pass path prefixes that never match, so scheduled
runs deterministically take the skip path — running the real detection
script (git history scan, output wiring, skip summary) without invoking
the mutation tools themselves. The mutation-run path is covered by the
``workflow_scripts`` unit tests and by pilot-repository validation; see
the ExecPlan's Decision Log.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from .conftest import (
    FIXTURES_DIR,
    ActConfig,
    run_act,
    skip_unless_act,
    skip_unless_workflow_tests,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def artefact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artefacts"


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_schedule_takes_skip_path(artefact_dir: Path) -> None:
    """A scheduled cargo run without matching changes skips cleanly."""
    event_path = FIXTURES_DIR / "schedule.event.json"
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act("test-mutation-cargo.yml", "schedule", "mutation", config)
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"mutation_detect_has_changes=\s*False", logs), (
        "detection did not report has_changes=False"
    )
    assert not re.search(r"cargo mutants", logs), (
        "mutation run should not start on the skip path"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_mutmut_schedule_takes_skip_path(artefact_dir: Path) -> None:
    """A scheduled mutmut run without matching changes skips cleanly."""
    event_path = FIXTURES_DIR / "schedule.event.json"
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act("test-mutation-mutmut.yml", "schedule", "mutation", config)
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"mutation_detect_has_changes=\s*False", logs), (
        "detection did not report has_changes=False"
    )
    assert not re.search(r"mutmut run", logs), (
        "mutation run should not start on the skip path"
    )
