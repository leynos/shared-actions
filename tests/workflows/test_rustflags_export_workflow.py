"""Act-backed tests for the rustflags inputs at the composite-action boundary.

The unit tests run the export step's shell fragment directly against a fake
``GITHUB_ENV``. These run the actions through a real runner instead, so they
cover what that cannot: that the heredoc the step writes is accepted by the
runner's environment-file parser and that the resulting ``RUSTFLAGS`` is
visible to a later step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from .conftest import (
    FIXTURES_DIR,
    ActConfig,
    run_act,
    skip_unless_act,
    skip_unless_workflow_tests,
)

WORKFLOW = "test-rustflags-export.yml"
WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / WORKFLOW
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


def test_setup_rust_toolchain_workflow_shape() -> None:
    """The runner job exercises the intended local setup-rust path."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{WORKFLOW_PATH} must contain a YAML mapping"
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), f"{WORKFLOW_PATH} must define a jobs mapping"
    job = jobs.get("setup-rust-toolchain-available")
    assert isinstance(job, dict), (
        "workflow must define the setup-rust-toolchain-available job"
    )
    steps = job.get("steps")
    assert isinstance(steps, list), (
        "setup-rust-toolchain-available must define a steps collection"
    )
    assert all(isinstance(step, dict) for step in steps), (
        "every setup-rust-toolchain-available step must be a mapping"
    )

    removal_steps = [
        step
        for step in steps
        if step.get("name") == "Remove the preinstalled stable toolchain"
    ]
    assert len(removal_steps) == 1, (
        "expected exactly one Remove the preinstalled stable toolchain step"
    )
    removal_step = removal_steps[0]
    removal_script = removal_step["run"]
    assert "rustup toolchain uninstall stable" in removal_script, (
        "removal must uninstall the preinstalled stable toolchain"
    )
    assert re.search(
        r"if rustup run stable rustc --version >/dev/null 2>&1; then.*?exit 1.*?fi",
        removal_script,
        flags=re.DOTALL,
    ), "removal must fail when stable rustc remains available"

    setup_steps = [step for step in steps if step.get("name") == "Setup stable Rust"]
    assert len(setup_steps) == 1, "expected exactly one Setup stable Rust step"
    setup_step = setup_steps[0]
    assert steps.index(removal_step) < steps.index(setup_step), (
        "the stable toolchain must be removed before setup-rust runs"
    )
    assert setup_step["uses"] == "./.github/actions/setup-rust", (
        "Setup stable Rust must call the local setup-rust action"
    )
    assert setup_step["with"] == {
        "toolchain": "stable",
        "install-binstall": "false",
        "use-sccache": "false",
    }, "Setup stable Rust must select the isolated stable toolchain path"

    verify_steps = [
        step
        for step in steps
        if step.get("name") == "Verify Rust tools remain available"
    ]
    assert len(verify_steps) == 1, (
        "expected exactly one Verify Rust tools remain available step"
    )
    verify_step = verify_steps[0]
    script = verify_step["run"]
    assert "rustc --version" in script, "verification must execute rustc"
    assert "cargo --version" in script, "verification must execute cargo"
    assert 'test -n "${rustc_version}"' in script, (
        "verification must assert that rustc returned a version"
    )
    assert 'test -n "${cargo_version}"' in script, (
        "verification must assert that cargo returned a version"
    )


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


@skip_unless_act
@skip_unless_workflow_tests
def test_setup_rust_exposes_rust_tools_to_later_steps(artefact_dir: Path) -> None:
    """A supported Linux setup leaves rustc and cargo available downstream."""
    logs = _run("setup-rust-toolchain-available", artefact_dir)

    assert re.search(r"setup_rust_toolchain=\[stable-[^]]+", logs), (
        f"setup-rust did not select the required stable toolchain:\n{logs}"
    )
    assert re.search(r"setup_rust_rustc=\[rustc \d+\.\d+\.\d+", logs), (
        f"rustc was not available after setup-rust:\n{logs}"
    )
    assert re.search(r"setup_rust_cargo=\[cargo \d+\.\d+\.\d+", logs), (
        f"cargo was not available after setup-rust:\n{logs}"
    )
