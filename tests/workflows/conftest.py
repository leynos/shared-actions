"""Shared fixtures and utilities for behavioural workflow tests."""

from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path

import pytest
from plumbum import CommandNotFound, ProcessTimedOut, local

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _act_available() -> bool:
    """Return True if act is installed and runnable."""
    return shutil.which("act") is not None


def _container_runtime_available() -> bool:
    """Return True if a container runtime (docker/podman) is available."""
    for runtime in ("docker", "podman"):
        if shutil.which(runtime) is None:
            continue
        try:
            cmd = local[runtime]
            cmd["info"].run(timeout=10, retcode=None)
        except (ProcessTimedOut, CommandNotFound, OSError):
            continue
        else:
            return True
    return False


def _workflow_tests_enabled() -> bool:
    """Return True if ACT_WORKFLOW_TESTS is set."""
    return os.environ.get("ACT_WORKFLOW_TESTS", "").lower() in ("1", "true", "yes")


skip_unless_act = pytest.mark.skipif(
    not (_act_available() and _container_runtime_available()),
    reason="act or container runtime not available",
)

skip_unless_workflow_tests = pytest.mark.skipif(
    not _workflow_tests_enabled(),
    reason="ACT_WORKFLOW_TESTS not set (opt-in required)",
)


@dataclasses.dataclass(slots=True)
class ActConfig:
    """Configuration for running act against a workflow."""

    artifact_dir: Path
    event_path: Path | None = None
    env: dict[str, str] | None = None
    timeout: int = 300


def run_act(
    workflow: str,
    event: str,
    job: str,
    config: ActConfig,
) -> tuple[int, str]:
    """Run act against a workflow and return the exit code and logs.

    Parameters
    ----------
    workflow
        Path to the workflow file relative to .github/workflows/.
    event
        GitHub event type (push, pull_request, workflow_call, etc.).
    job
        Job name to run.
    config
        Execution configuration including artifact directory, event path,
        environment variables, and timeout.

    Returns
    -------
    tuple[int, str]
        Exit code and combined stdout/stderr logs.
    """
    config.artifact_dir.mkdir(parents=True, exist_ok=True)

    event_path = config.event_path
    if event_path is None:
        event_path = FIXTURES_DIR / f"{event}.event.json"

    act = local["act"]
    cmd = act[
        event,
        "-W",
        f".github/workflows/{workflow}",
        "-j",
        job,
        "-e",
        str(event_path),
        "-P",
        "ubuntu-latest=catthehacker/ubuntu:act-latest",
        "--artifact-server-path",
        str(config.artifact_dir),
        "--json",
        "-b",
    ]

    run_env = os.environ.copy()
    if config.env:
        run_env.update(config.env)

    try:
        retcode, stdout, stderr = cmd.run(
            timeout=config.timeout, env=run_env, retcode=None
        )
        return retcode, stdout + "\n" + stderr
    except ProcessTimedOut:
        return 1, f"act timed out after {config.timeout}s"
