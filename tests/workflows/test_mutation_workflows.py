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
def test_mutation_cargo_installs_mdtablefix_before_setup(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """The opt-in installer exposes its binary to caller setup commands."""
    event_path = _workflow_dispatch_event(tmp_path, "install")
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act(
        "test-mutation-cargo.yml", "workflow_dispatch", "mutation", config
    )
    assert code != 0, f"the setup sentinel should stop act before mutation:\n{logs}"
    assert "mdtablefix 0.6.1" in logs, f"installer output missing:\n{logs}"
    assert "act-test-stop-after-mdtablefix" in logs, f"setup did not run:\n{logs}"
    assert "Run mutation testing" not in logs, (
        "the integration fixture must stop before mutation execution"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_opt_out_does_not_install_mdtablefix(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """The default opt-out leaves mdtablefix absent from the runner PATH."""
    event_path = _workflow_dispatch_event(tmp_path, "skip")
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act(
        "test-mutation-cargo.yml", "workflow_dispatch", "mutation", config
    )
    assert code != 0, f"the setup sentinel should stop act before mutation:\n{logs}"
    assert "act-test-mdtablefix-absent" in logs, f"tool was present:\n{logs}"
    assert '"stage":"Main","step":"Install mdtablefix"' not in logs, (
        f"opt-out ran installer:\n{logs}"
    )
    assert "Run mutation testing" not in logs, (
        "the integration fixture must stop before mutation execution"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_mutation_cargo_propagates_mdtablefix_validation_failure(
    artefact_dir: Path, tmp_path: Path
) -> None:
    """A bad forwarded checksum fails the installer before caller setup."""
    event_path = _workflow_dispatch_event(tmp_path, "invalid-digest")
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act(
        "test-mutation-cargo.yml", "workflow_dispatch", "mutation", config
    )
    assert code != 0, f"invalid installer input should fail act:\n{logs}"
    assert "sha256 must be empty or 64 hexadecimal digits" in logs, (
        f"installer validation failure missing:\n{logs}"
    )
    assert "act-test-setup-ran-after-installer-failure" not in logs, (
        f"caller setup ran after installer failure:\n{logs}"
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
