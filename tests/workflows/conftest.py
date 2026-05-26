"""Shared fixtures and utilities for behavioural workflow tests."""

from __future__ import annotations

from pathlib import Path
import dataclasses
import functools
import os
import shutil
import socket
import tempfile
import typing as typ
import urllib.parse

from plumbum import CommandNotFound, ProcessTimedOut, local
import pytest

from bool_utils import coerce_bool

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DOCKER_CONTAINERS_PATH = "/v1.41/containers/json?all=true"
_DOCKER_API_TIMEOUT_SECONDS = 10.0


@dataclasses.dataclass(frozen=True, slots=True)
class ActRuntimeStatus:
    """Availability result for the runtime path that act will actually use."""

    available: bool
    reason: str
    env: dict[str, str]


def _act_command(environ: cabc.Mapping[str, str] | None = None) -> str:
    """Return the act executable configured for workflow tests."""
    source = os.environ if environ is None else environ
    return source.get("ACT", "act")


def _command_available(command: str) -> bool:
    """Return True when *command* names an executable file or PATH command."""
    command_path = Path(command)
    if command_path.parent != Path():
        return command_path.is_file() and os.access(command_path, os.X_OK)
    return shutil.which(command) is not None

def _act_available() -> bool:
    """Return True if act is installed and runnable."""
    return _command_available(_act_command())


def _act_available() -> bool:
    """Return True if act is installed and runnable."""
    return _command_available(_act_command())


def _default_podman_socket(environ: cabc.Mapping[str, str]) -> Path:
    """Return the default rootless Podman Docker-compatible socket path."""
    runtime_dir = environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "podman" / "podman.sock"
    return Path("/run/user") / str(os.getuid()) / "podman" / "podman.sock"


def _read_unix_http(socket_path: Path, path: str) -> tuple[int, str]:
    """Issue a small HTTP request over a Unix socket and return status/body."""
    request = (
        f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    chunks: list[bytes] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(_DOCKER_API_TIMEOUT_SECONDS)
        client.connect(str(socket_path))
        client.sendall(request)
        while True:
            chunk = client.recv(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

    response = b"".join(chunks)
    head, _separator, body = response.partition(b"\r\n\r\n")
    head_lines = head.splitlines()
    if not head_lines:
        return 0, response.decode("utf-8", errors="replace")
    status_line = head_lines[0].decode("ascii", errors="replace")
    parts = status_line.split(maxsplit=2)
    if len(parts) < 2:
        return 0, response.decode("utf-8", errors="replace")
    try:
        status_code = int(parts[1])
    except ValueError:
        return 0, response.decode("utf-8", errors="replace")
    return status_code, body.decode("utf-8", errors="replace")


def _docker_host_usable(docker_host: str) -> tuple[bool, str]:
    """Return whether act can list containers through *docker_host*."""
    parsed = urllib.parse.urlparse(docker_host)
    if parsed.scheme != "unix":
        return True, ""

    socket_path = Path(urllib.parse.unquote(parsed.path))
    if not socket_path.exists():
        return False, f"Docker API socket does not exist: {socket_path}"

    try:
        status, body = _read_unix_http(socket_path, _DOCKER_CONTAINERS_PATH)
    except OSError as exc:
        return False, f"Docker API socket is not reachable: {exc}"

    if status != 200:
        detail = body.strip() or f"HTTP {status}"
        return False, f"Docker API cannot list containers: {detail}"
    return True, ""

def _container_runtime_available() -> bool:
    """Return True if a container runtime (docker/podman) is available."""
    return _probe_act_runtime().available


def _container_runtime_available() -> bool:
    """Return True if a container runtime (docker/podman) is available."""
    return _probe_act_runtime().available


def _command_succeeds(command: str, *args: str) -> bool:
    """Return True when a command exits successfully within the probe timeout."""
    try:
        cmd = local[command]
        retcode, _, _ = cmd[list(args)].run(timeout=10, retcode=None)
    except (ProcessTimedOut, CommandNotFound, OSError):
        return False
    return retcode == 0


def _docker_cli_available() -> bool:
    """Return True when the Docker CLI is present and the daemon is reachable."""
    return (
        shutil.which("docker") is not None
        and _command_succeeds("docker", "info")
        and _command_succeeds("docker", "ps", "-a")
    )


def _probe_act_runtime(
    environ: cabc.Mapping[str, str] | None = None,
) -> ActRuntimeStatus:
    """Probe the container runtime path used by act workflow tests."""
    source = os.environ if environ is None else environ
    act_command = _act_command(source)
    if not _command_available(act_command):
        return ActRuntimeStatus(
            available=False,
            reason=f"act executable not found: {act_command}",
            env={},
        )

    if docker_host := source.get("DOCKER_HOST"):
        usable, reason = _docker_host_usable(docker_host)
        return ActRuntimeStatus(available=usable, reason=reason, env={})

    if _docker_cli_available():
        return ActRuntimeStatus(available=True, reason="", env={})

    if not shutil.which("podman"):
        return ActRuntimeStatus(
            available=False,
            reason="docker or podman runtime not available",
            env={},
        )

    podman_socket = _default_podman_socket(source)
    docker_host = f"unix://{podman_socket}"
    usable, reason = _docker_host_usable(docker_host)
    if not usable:
        return ActRuntimeStatus(
            available=False,
            reason=(
                f"podman Docker API is not usable for act: {reason}. "
                "Start the user podman.socket if the socket is missing. If the "
                "API reports 'container not known', repair or remove stale "
                "Podman containers stuck in Removing state before rerunning."
            ),
            env={},
        )
    return ActRuntimeStatus(
        available=True,
        reason="",
        env={"DOCKER_HOST": docker_host},
    )


@functools.cache
def _get_act_runtime_status() -> ActRuntimeStatus:
    """Return the runtime probe result, probing lazily on first call.

    Probing is deferred so that tests that modify ACT, DOCKER_HOST
    or related environment variables see the updated configuration.
    """
    return _probe_act_runtime()


@pytest.fixture(autouse=True)
def _reset_act_runtime_cache() -> typ.Generator[None, None, None]:
    """Clear the cached act runtime probe result after each test.

    Ensures that tests which modify ``ACT``, ``DOCKER_HOST``, or related
    environment variables via monkeypatch do not have their changes obscured
    by a stale cached result from a prior test.
    """
    yield
    _get_act_runtime_status.cache_clear()


def _workflow_tests_enabled() -> bool:
    """Return True if ACT_WORKFLOW_TESTS is set."""
    return coerce_bool(os.environ.get("ACT_WORKFLOW_TESTS"), default=False)


skip_unless_act = pytest.mark.skip_unless_act

skip_unless_workflow_tests = pytest.mark.skipif(
    not _workflow_tests_enabled(),
    reason="ACT_WORKFLOW_TESTS not set (opt-in required)",
)


def pytest_configure(config: pytest.Config) -> None:
    """Register workflow test markers."""
    config.addinivalue_line("markers", "skip_unless_act: skip unless act can run")


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip act tests when the runtime probe fails."""
    if "skip_unless_act" not in item.keywords:
        return
    _get_act_runtime_status.cache_clear()
    status = _get_act_runtime_status()
    if not status.available:
        pytest.skip(status.reason or "act or container runtime not available")


@pytest.fixture
def temp_base_dir() -> Path:
    """Return the system temporary directory as a Path."""
    return Path(tempfile.gettempdir())


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
    run_env.update(_get_act_runtime_status().env)
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

    act = local[_act_command(run_env)]
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
