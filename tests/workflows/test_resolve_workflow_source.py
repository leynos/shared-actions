"""Integration tests for the resolve-workflow-source composite action.

Under act the fixture exercises one branch and proves the other did not
run: the act short-circuit (workspace as workflow source, no checkout)
fires, and the OIDC fail-fast is skipped. The fail-fast is unreachable
here by construction -- act sets `ACT=true`, which is the condition the
fail-fast half is guarded against -- and it is the case only a real
`workflow_dispatch` can exercise. The OIDC happy path is validated by
every real run of the reusable workflows that consume the action.
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
def test_resolve_workflow_source_branches(artefact_dir: Path) -> None:
    """Both the act short-circuit and OIDC fail-fast branches behave."""
    event_path = FIXTURES_DIR / "workflow_dispatch.event.json"
    config = ActConfig(artefact_dir=artefact_dir, event_path=event_path)
    code, logs = run_act(
        "test-resolve-workflow-source.yml", "workflow_dispatch", "resolve", config
    )
    assert code == 0, f"act failed:\n{logs}"
    assert re.search(r"resolve_act_branch=ok", logs), (
        "act short-circuit branch assertions did not run"
    )
    # The OIDC fail-fast cannot run here and is not supposed to. Act forces
    # `ACT=true` into the composite's own environment, so the guard that
    # selects the fail-fast half is false under act whatever the fixture
    # does, and a run that reached it would mean the guard had stopped
    # separating the two branches. The fixture reports the skip, so assert
    # on that: it is the evidence that exactly one half fired.
    assert re.search(r"resolve_oidc_failfast=skipped", logs), (
        "OIDC fail-fast branch did not report itself skipped under act"
    )
    assert not re.search(r"OpenID Connect \(OIDC\) env vars not available", logs), (
        "the OIDC fail-fast branch ran under act, where it is unreachable"
    )
