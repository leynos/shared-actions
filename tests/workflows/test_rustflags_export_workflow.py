"""Act-backed tests for the rustflags inputs at the composite-action boundary.

The unit tests run the export step's shell fragment directly against a fake
``GITHUB_ENV``. These run the actions through a real runner instead, so they
cover what that cannot: that the heredoc the step writes is accepted by the
runner's environment-file parser and that the resulting ``RUSTFLAGS`` is
visible to a later step.
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

WORKFLOW = "test-rustflags-export.yml"
# The workflow runs on the release event because the nested setup-rust skips
# sccache for releases, whose post-step is unreliable under act.
EVENT = "release"


@pytest.fixture
def artefact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artefacts"


def _run(job: str, artefact_dir: Path) -> str:
    """Run one job of the rustflags workflow and return its logs."""
    config = ActConfig(
        artefact_dir=artefact_dir,
        event_path=FIXTURES_DIR / f"{EVENT}.event.json",
        timeout=600,
    )
    code, logs = run_act(WORKFLOW, EVENT, job, config)
    assert code == 0, f"act failed:\n{logs}"
    return logs


@skip_unless_act
@skip_unless_workflow_tests
def test_rust_build_release_exports_rustflags_to_later_steps(
    artefact_dir: Path,
) -> None:
    """The exported value reaches a step running after the action."""
    logs = _run("rust-build-release-exports", artefact_dir)

    assert re.search(r"rbr_rustflags=\[-D warnings -C debuginfo=0\]", logs), (
        f"the caller's rustflags did not reach a later step:\n{logs}"
    )
    assert "RUSTFLAGS exported from the rustflags input" in logs, (
        f"the export step did not report a successful export:\n{logs}"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_rust_build_release_defers_to_inherited_rustflags(
    artefact_dir: Path,
) -> None:
    """A job-level RUSTFLAGS survives a conflicting rustflags input."""
    logs = _run("rust-build-release-defers-to-inherited", artefact_dir)

    assert re.search(r"inherited_rustflags=\[-D warnings\]", logs), (
        f"the inherited RUSTFLAGS was not preserved:\n{logs}"
    )
    assert "debuginfo=2" not in logs.split("inherited_rustflags=")[-1], (
        f"the input displaced the inherited value:\n{logs}"
    )
    assert "leaving the inherited value in place" in logs, (
        f"the export step did not report deferring to the inherited value:\n{logs}"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_setup_rust_forwards_rustflags_to_later_steps(artefact_dir: Path) -> None:
    """setup-rust's own input reaches a step running after the action.

    The jobs above pin a remote setup-rust revision, so this is the only
    coverage that runs the local action.
    """
    logs = _run("setup-rust-exports", artefact_dir)

    assert re.search(r"setup_rust_rustflags=\[-D warnings -C debuginfo=0\]", logs), (
        f"setup-rust did not forward its rustflags input:\n{logs}"
    )


@skip_unless_act
@skip_unless_workflow_tests
def test_setup_rust_leaves_an_inherited_rustflags_alone(artefact_dir: Path) -> None:
    """An inherited RUSTFLAGS survives a conflicting setup-rust input.

    setup-rust forwards the input unconditionally, so the deferral is the
    nested setup-rust-toolchain's doing rather than a guard of our own. This
    pins that behaviour, which a toolchain-action bump could otherwise change
    silently.
    """
    logs = _run("setup-rust-with-inherited", artefact_dir)

    assert re.search(r"setup_rust_inherited_rustflags=\[-D warnings\]", logs), (
        f"the inherited RUSTFLAGS was not preserved:\n{logs}"
    )
    assert "debuginfo=2" not in logs.split("setup_rust_inherited_rustflags=")[-1], (
        f"the input displaced the inherited value:\n{logs}"
    )
