"""Integration tests for the resolve-workflow-source composite action.

The wrapper workflow exercises the two branches reachable outside real
GitHub infrastructure: the act short-circuit (workspace as workflow
source, no checkout) and the OIDC fail-fast when the token endpoint is
unavailable. The OIDC happy path is validated by every real run of the
reusable workflows that consume the action.
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
def artifact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artifacts"


@skip_unless_act
@skip_unless_workflow_tests
def test_resolve_workflow_source_branches(artifact_dir: Path) -> None:
    """Both the act short-circuit and OIDC fail-fast branches behave."""
    event_path = FIXTURES_DIR / "workflow_dispatch.event.json"
    config = ActConfig(artifact_dir=artifact_dir, event_path=event_path)
    code, logs = run_act(
        "test-resolve-workflow-source.yml", "workflow_dispatch", "resolve", config
    )
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"resolve_act_branch=ok", logs), (
        "act short-circuit branch assertions did not run"
    )
    assert re.search(r"resolve_oidc_failfast=ok", logs), (
        "OIDC fail-fast branch assertions did not run"
    )
    assert re.search(r"OpenID Connect \(OIDC\) env vars not available", logs), (
        "fail-fast diagnostic message not found in logs"
    )
