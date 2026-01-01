"""Shared fixtures and utilities for behavioural workflow tests."""

from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path

import pytest
from plumbum import CommandNotFound, ProcessTimedOut, local

from bool_utils import coerce_bool

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
            retcode, _, _ = cmd["info"].run(timeout=10, retcode=None)
        except (ProcessTimedOut, CommandNotFound, OSError):
            continue
        else:
            if retcode == 0:
                return True
    return False


def _workflow_tests_enabled() -> bool:
    """Return True if ACT_WORKFLOW_TESTS is set."""
    return coerce_bool(os.environ.get("ACT_WORKFLOW_TESTS"), default=False)


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
    container_env: dict[str, str] | None = None
    timeout: int = 300


@dataclasses.dataclass(slots=True)
class ActInvocation:
    """Parameters for a single act invocation."""

    workflow: str
    event: str
    job: str
    event_path: Path
    artifact_dir: Path
    container_env: dict[str, str]


def _resolve_event_path(config: ActConfig, event: str) -> Path:
    """Resolve the event path from config or default fixture."""
    if config.event_path is not None:
        return config.event_path
    return FIXTURES_DIR / f"{event}.event.json"


def _build_container_env(config: ActConfig, run_env: dict[str, str]) -> dict[str, str]:
    """Build the container environment dict with UV forwarding."""
    merged_container_env: dict[str, str] = {}
    if config.container_env:
        merged_container_env.update(config.container_env)
    # Forward uv's project environment override into the act container.
    uv_env_key = "UV_PROJECT_ENVIRONMENT"
    if uv_env_key in run_env and uv_env_key not in merged_container_env:
        merged_container_env[uv_env_key] = run_env[uv_env_key]
    return merged_container_env


def _build_act_args(invocation: ActInvocation) -> list[str]:
    """Build the list of arguments for the act command."""
    args = [
        invocation.event,
        "-W",
        f".github/workflows/{invocation.workflow}",
        "-j",
        invocation.job,
        "-e",
        str(invocation.event_path),
        "-P",
        "ubuntu-latest=catthehacker/ubuntu:act-latest",
        "--artifact-server-path",
        str(invocation.artifact_dir),
        "--json",
        "-b",
    ]
    for key, value in invocation.container_env.items():
        args.extend(["--env", f"{key}={value}"])
    return args


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

    event_path = _resolve_event_path(config, event)

    run_env = os.environ.copy()
    if config.env:
        run_env.update(config.env)

    container_env = _build_container_env(config, run_env)
    invocation = ActInvocation(
        workflow=workflow,
        event=event,
        job=job,
        event_path=event_path,
        artifact_dir=config.artifact_dir,
        container_env=container_env,
    )
    args = _build_act_args(invocation)

    act = local["act"]
    cmd = act
    for arg in args:
        cmd = cmd[arg]

    try:
        retcode, stdout, stderr = cmd.run(
            timeout=config.timeout, env=run_env, retcode=None
        )
        return retcode, stdout + "\n" + stderr
    except ProcessTimedOut:
        return 1, f"act timed out after {config.timeout}s"
