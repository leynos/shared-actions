"""Integration tests for the dependabot auto-merge reusable workflow."""

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
def artifact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artifacts"


@skip_unless_act
@skip_unless_workflow_tests
def test_dependabot_automerge_dry_run(artifact_dir: Path) -> None:
    """Dependabot workflow logs dry-run readiness under act."""
    event_path = FIXTURES_DIR / "pull_request_dependabot.event.json"
    config = ActConfig(artifact_dir=artifact_dir, event_path=event_path)
    code, logs = run_act(
        "test-dependabot-automerge.yml", "pull_request", "automerge", config
    )
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"automerge_status=\s*dry-run", logs, flags=re.IGNORECASE), (
        "automerge_status=dry-run not found in logs"
    )
    assert re.search(r"automerge_reason=eligible", logs, flags=re.IGNORECASE), (
        "automerge_reason=eligible not found in logs"
    )
