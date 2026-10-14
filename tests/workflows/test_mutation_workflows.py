"""Integration tests for the mutation-testing reusable workflows.

These tests exercise the mutation workflow under act. Scheduled runs take
the change-detection skip path; dispatch fixtures reach the mdtablefix setup
to verify install, opt-out and installer-failure behavior, then stop before
mutation execution.
"""

from __future__ import annotations

import json
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


def _workflow_dispatch_event(tmp_path: Path, mode: str) -> Path:
    """Write a workflow-dispatch event selecting one installer test mode."""
    event_path = tmp_path / f"{mode}.event.json"
    event_path.write_text(
        json.dumps({"inputs": {"act-mdtablefix-mode": mode}}), encoding="utf-8"
    )
    return event_path


def _run_mutation_workflow(
    artefact_dir: Path, workflow: str, event_name: str, event_path: Path
) -> tuple[int, str]:
    """Run a mutation workflow with the supplied event fixture."""
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    return run_act(workflow, event_name, "mutation", config)


def _assert_mutation_did_not_start(logs: str) -> None:
    """Check that act never executed the mutation command."""
    assert "cargo mutants" not in logs, "mutation execution started before the sentinel"


@skip_unless_act
@skip_unless_workflow_tests
@pytest.mark.parametrize(
    ("workflow", "mutation_command"),
    [
        pytest.param("test-mutation-cargo.yml", "cargo mutants", id="cargo-mutants"),
        pytest.param("test-mutation-mutmut.yml", "mutmut run", id="mutmut"),
    ],
)
def test_mutation_schedule_takes_skip_path(
    artefact_dir: Path, workflow: str, mutation_command: str
) -> None:
    """Scheduled mutation workflows without changes skip cleanly."""
    event_path = FIXTURES_DIR / "schedule.event.json"
    code, logs = _run_mutation_workflow(artefact_dir, workflow, "schedule", event_path)
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"mutation_detect_has_changes=\s*False", logs), (
        "detection did not report has_changes=False"
    )
    assert not re.search(re.escape(mutation_command), logs), (
        f"{mutation_command} should not start on the skip path"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_installs_mdtablefix_before_setup(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """The opt-in installer exposes its binary to caller setup commands."""
    event_path = _workflow_dispatch_event(tmp_path, "install")
    code, logs = _run_mutation_workflow(
        artefact_dir, "test-mutation-cargo.yml", "workflow_dispatch", event_path
    )
    assert code != 0, f"the setup sentinel should stop act before mutation:\n{logs}"
    assert "mdtablefix 0.6.1" in logs, f"installer output missing:\n{logs}"
    assert "act-test-stop-after-mdtablefix" in logs, f"setup did not run:\n{logs}"
    _assert_mutation_did_not_start(logs)


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_opt_out_does_not_install_mdtablefix(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """The default opt-out leaves mdtablefix absent from the runner PATH."""
    event_path = _workflow_dispatch_event(tmp_path, "skip")
    code, logs = _run_mutation_workflow(
        artefact_dir, "test-mutation-cargo.yml", "workflow_dispatch", event_path
    )
    assert code != 0, f"the setup sentinel should stop act before mutation:\n{logs}"
    assert "act-test-mdtablefix-absent" in logs, f"tool was present:\n{logs}"
    assert "mdtablefix 0.6.1" not in logs, f"opt-out installed mdtablefix:\n{logs}"
    _assert_mutation_did_not_start(logs)


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_propagates_mdtablefix_validation_failure(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """A bad forwarded checksum fails the installer before caller setup."""
    event_path = _workflow_dispatch_event(tmp_path, "invalid-digest")
    code, logs = _run_mutation_workflow(
        artefact_dir, "test-mutation-cargo.yml", "workflow_dispatch", event_path
    )
    assert code != 0, f"invalid installer input should fail act:\n{logs}"
    assert "sha256 must be empty or 64 hexadecimal digits" in logs, (
        f"installer validation failure missing:\n{logs}"
    )
    assert "act-test-setup-ran-after-installer-failure" not in logs, (
        f"caller setup ran after installer failure:\n{logs}"
    )
