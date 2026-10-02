"""Shared fixtures and utilities for behavioural workflow tests."""

from __future__ import annotations

import dataclasses
import functools
import os
import re
import shutil
import socket
import tempfile
import typing as typ
import urllib.parse
from pathlib import Path

import pytest
from plumbum import CommandNotFound, ProcessTimedOut, local

from bool_utils import coerce_bool

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).parent / "fixtures"
#: The checkout act bind-mounts. Taken from this file's own location rather
#: than from the process's working directory, so the probe below resolves the
#: same repository however the suite was started.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DOCKER_CONTAINERS_PATH = "/v1.41/containers/json?all=true"
_DOCKER_API_TIMEOUT_SECONDS = 10.0

#: The ceiling on asking git where its object store is. The command reads a
#: path and prints it; anything slower than this is not git answering, and a
#: queued prompt for credentials must not hold the suite.
_GIT_PROBE_TIMEOUT_SECONDS = 10.0


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


_ON_WINDOWS = os.name == "nt"
_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"
# PATHEXT is semicolon-separated on Windows whatever os.pathsep says on the
# host running these tests, so the separator is written out rather than
# borrowed from the platform.
_PATHEXT_SEPARATOR = ";"


#: Suffixes `PATHEXT` commonly carries that name a script rather than a
#: program: Windows runs them by handing them to an interpreter, and the
#: probe's caller does not. `_run_act` passes the resolved path straight
#: to `plumbum.local[...]`, which spawns it as a process and does not
#: select `powershell` or `wscript`, so a `.PS1` here would pass the
#: availability check and then fail at process creation with a message
#: about the file rather than about the probe. Refusing them keeps the
#: check's answer and the caller's behaviour the same thing.
_INTERPRETED_SUFFIXES: typ.Final[frozenset[str]] = frozenset(
    {
        ".ps1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".msc",
        ".cpl",
        ".py",
        ".pyw",
    }
)


# `PATHEXT` is the list Windows searches, not the list of files a caller can
# spawn. The interpreted suffixes are filtered out because the caller hands
# the resolved path straight to plumbum, which selects no interpreter.
def _windows_executable_suffixes(
    environ: cabc.Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Return the lower-cased suffixes Windows would spawn directly."""
    source = os.environ if environ is None else environ
    raw = source.get("PATHEXT") or _DEFAULT_PATHEXT
    return frozenset(
        suffix.strip().lower()
        for suffix in raw.split(_PATHEXT_SEPARATOR)
        if suffix.strip() and suffix.strip().lower() not in _INTERPRETED_SUFFIXES
    )


# Windows has no execute permission bit, so `os.access(path, os.X_OK)` answers
# True for every readable file there. Executability on that platform is carried
# by the suffix, which is what PATHEXT enumerates.
def _is_executable_file(
    path: Path,
    *,
    on_windows: bool = _ON_WINDOWS,
    environ: cabc.Mapping[str, str] | None = None,
) -> bool:
    """Return True when *path* is a file the operating system would run.

    *environ* supplies PATHEXT on Windows. It defaults to the process
    environment, read in `_windows_executable_suffixes` and nowhere else,
    so a caller or a test can pass its own mapping instead.
    """
    if not path.is_file():
        return False
    if on_windows:
        return path.suffix.lower() in _windows_executable_suffixes(environ)
    return os.access(path, os.X_OK)


def _command_available(
    command: str, *, environ: cabc.Mapping[str, str] | None = None
) -> bool:
    """Return True when *command* names an executable file or PATH command.

    *environ* is passed through to `_is_executable_file` for a path.
    """
    command_path = Path(command)
    if command_path.parent != Path():
        return _is_executable_file(command_path, environ=environ)
    return shutil.which(command) is not None


def _act_available(environ: cabc.Mapping[str, str] | None = None) -> bool:
    """Return True if act is installed and runnable.

    *environ* reaches both the `ACT` lookup and the PATHEXT check.
    """
    source = os.environ if environ is None else environ
    return _command_available(_act_command(source), environ=source)


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
    if not _command_available(act_command, environ=source):
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

    artefact_dir: Path
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
    artefact_dir: Path
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


#: The image every Linux label is given. Both labels get it because every
#: fixture that names either is a scripted orchestration rather than a
#: measurement of the runner: the case is about which steps run and what
#: they print, not about the machine. An Ubicloud label changed here is a
#: label whose workflows no longer need it before it needs an image of its
#: own.
#:
#: `rust-latest` rather than `act-latest` because the lane exercises
#: `install-whitaker`, whose installer extracts the pinned `cargo-dylint`
#: archive and then probes `cargo dylint --version` to verify it. `act-latest`
#: ships no cargo, rustc or rustup anywhere on the filesystem, so the probe
#: fails and, because the action passes `--no-source-fallback`, the case
#: aborts with "the repository install failed verification" without testing
#: anything. `rust-latest` is the same Ubuntu base with the toolchain added.
_ACT_IMAGE: typ.Final[str] = "catthehacker/ubuntu:rust-latest"

#: The platforms act may be given an image for: the Linux labels this
#: repository's fixtures run on. Taken from the shared vocabulary in
#: `_workflow_reading` rather than written out, because the two lists are
#: the same list -- a fixture moved to a label missing here is a fixture
#: that runs nowhere and still passes.
_LINUX_PLATFORMS: typ.Final[frozenset[str]] = reading.RECOGNIZED_LINUX_LABELS | {
    reading.UBICLOUD_LINUX
}

#: A whole-string `${{ matrix.<key> }}` reference, with the key captured.
_MATRIX_BINDING: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*matrix\.(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}$"
)

#: The quoted strings inside a `&&`/`||` expression, in the order written.
#: A quoted string may be a runner label or may be something else the
#: expression compares against -- `github.event_name == 'schedule'` quotes
#: the event name -- so the two are told apart by the label vocabularies
#: rather than by the quoting.
_LABEL_LITERAL: typ.Final[re.Pattern[str]] = re.compile(r"'([^']*)'")

#: Every runner label this repository recognizes, Linux and otherwise. A
#: quoted string in a `runs-on` expression that is one of these is read as a
#: label; one that is not is read as a value the expression compares against.
_RUNNER_LABELS: typ.Final[frozenset[str]] = (
    reading.RECOGNIZED_LINUX_LABELS | reading.RECOGNIZED_OTHER_LABELS
)


def _single_label(runs_on: object) -> str | None:
    """Return *runs_on* when it names a runner label rather than an expression."""
    if not isinstance(runs_on, str):
        return None
    stripped = runs_on.strip()
    return stripped if not stripped.startswith("${{") else None


def _matrix_labels(matrix: object, key: str) -> set[str | None]:
    """Return every label *matrix* binds *key* to, across its `include` legs.

    Only the `include` spelling is read. A matrix written any other way binds
    a `runs-on` key to something this cannot see, and the job is refused, which
    is the safe direction: a label read wrong is a platform act is given no
    image for, and act then skips the job and reports a green run that
    executed nothing.
    """
    if not isinstance(matrix, dict):
        return set()
    return {
        _single_label(leg.get(key))
        for leg in matrix.get("include", [])
        if isinstance(leg, dict)
    }


def _labels_named_by(runs_on: str) -> list[str]:
    """Return every runner label in a `runs-on` expression, in the order written.

    A quoted string counts as a label when it is one the vocabulary knows
    and as a compared-against value otherwise, so `'schedule'` in
    `github.event_name == 'schedule'` is not mistaken for a runner. Both
    vocabularies are read, not just the Linux one, because a label outside
    the Linux set is exactly what act has no image for and what the caller
    has to be told about.
    """
    return [
        quoted for quoted in _LABEL_LITERAL.findall(runs_on) if quoted in _RUNNER_LABELS
    ]


def _resolves_to_one_platform(job: cabc.Mapping[str, object]) -> bool:
    """Return True when *job* runs on exactly one label, whatever selects it.

    This is what decides whether act can run the job at all. `-P` takes one
    image per platform, and a job whose runner is chosen at run time needs
    one for every label it can reach. A matrix binds its legs to more than
    one shape and is refused, which costs the harness nothing because every
    matrix job in this repository is a listed ceiling contract that the
    suite already reads from the text.

    Only one shape is accepted, and it is the one the fixtures use: an
    expression whose every label is a Linux label given the same image. An
    expression naming any other runner is refused even when a Linux label
    sits beside it, because the arm it names is an arm act would skip and
    the case would then sometimes test nothing.

    Both spellings are followed one level rather than refused as expressions,
    so `runs-on: ${{ matrix.os }}` is judged by the labels its matrix offers
    instead of by the fact that it is an expression. A binding that yields no
    label is refused, because a `runs-on` resolving to nothing does not say
    which image to use.
    """
    runs_on = job.get("runs-on")
    label = _single_label(runs_on)
    if label is not None:
        return label in _LINUX_PLATFORMS

    if not isinstance(runs_on, str):
        return False
    binding = _MATRIX_BINDING.match(runs_on.strip())
    if binding is not None:
        strategy = job.get("strategy")
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        labels = _matrix_labels(matrix, binding.group("key"))
        labels.discard(None)
        return labels == {reading.UBICLOUD_LINUX}

    named = set(_labels_named_by(runs_on))
    return bool(named) and named <= _LINUX_PLATFORMS


#: A `uses:` naming a workflow in this repository, with the file name
#: captured. A reusable-workflow call carries no `runs-on` of its own: its
#: jobs run on the labels the callee's jobs declare, so those are the labels
#: the harness owes act an image for.
_LOCAL_CALLEE: typ.Final[re.Pattern[str]] = re.compile(
    r"^\./?\.github/workflows/(?P<name>[A-Za-z0-9_.-]+\.ya?ml)$"
)


def _callee_jobs(uses: object) -> cabc.Mapping[str, object] | None:
    """Return the jobs of the local workflow *uses* calls, or None.

    None means the call is not to this repository's own workflow tree -- an
    external action, or a workflow reached by a ref -- and the harness cannot
    read which labels it will run on.
    """
    if not isinstance(uses, str):
        return None
    match = _LOCAL_CALLEE.match(uses.split("@", 1)[0])
    if match is None:
        return None
    return reading.load_workflow(match.group("name"))["jobs"]


def _require_an_image_for(workflow: str, job_id: str) -> None:
    """Refuse a case act has no image for, before act is given anything.

    act needs a `-P` entry for every label a job can reach, and prints
    `Skipping unsupported platform` with a successful exit when it has none:
    no step runs, every log assertion finds nothing, and the case fails
    somewhere far from the cause. The harness refuses up front instead, naming
    the workflow and the job.

    A job that calls another workflow has no `runs-on` of its own, so the
    callee's jobs are the ones judged: act runs those, on their labels.

    Raises
    ------
    ValueError
        If *job_id* is not a job of *workflow*, or if the job -- or a job of
        the workflow it calls -- runs on a label the harness maps to no image.
        Both are defects in the harness's own call rather than conditions that
        should skip in silence: a listed job that no longer exists would
        otherwise run nothing and pass.
    """
    document = reading.load_workflow(workflow)
    job = document["jobs"].get(job_id)
    if not isinstance(job, dict):
        msg = (
            f"{reading.identifier(workflow, job_id)} is not a job of "
            f"{workflow}, so this case would run nothing and pass"
        )
        raise TypeError(msg)

    callee = _callee_jobs(job.get("uses"))
    bodies = {f"{workflow}::{job_id}": job} if callee is None else dict(callee)
    unimageable = sorted(
        name for name, body in bodies.items() if not _resolves_to_one_platform(body)
    )
    if unimageable:
        msg = (
            f"{unimageable} run on a runner outside {sorted(_LINUX_PLATFORMS)}, "
            "so act has no image to be given for them; drop the case or map "
            "the label"
        )
        raise ValueError(msg)


def _git_common_dir(repository: Path) -> Path | None:
    """Return the object store *repository* resolves to, or None.

    None means the probe could not establish one -- git is absent, the
    path is not a checkout, or git did not answer in time -- and the caller
    should invoke act without the mount rather than fail a case over it. A
    checkout whose metadata git will not name is not one whose workflows
    need the metadata to exist inside the container; refusing here would
    turn a missing git into a test error about the harness.

    Asked with `-C <repository>`, which keeps the answer about the checkout
    rather than about whatever directory the suite was started in.
    `--path-format=absolute` rather than the default, because the default
    prints a path relative to the directory git was asked in --
    `--git-common-dir` prints the bare `.` at the root of an ordinary
    checkout -- which is not a host path a container can be given.
    """
    try:
        completed = local["git"]["-C", str(repository)][
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ].run(timeout=_GIT_PROBE_TIMEOUT_SECONDS, retcode=None)
    except (ProcessTimedOut, CommandNotFound, OSError):
        return None
    if completed[0] != 0:
        return None
    reported = completed[1].strip()
    if not reported or reported == "/":
        # `/` is what git prints for a bare repository named by `--git-dir`:
        # the mount it would ask for is the host's whole filesystem.
        return None
    return Path(reported)


def _git_common_dir_mount(repository: Path) -> str | None:
    """Return the container option that makes *repository*'s git usable, or None.

    act bind-mounts the checkout at the path it occupies on the host, so a
    step can read the working tree and cannot follow a `.git` file out of
    it. A linked worktree's `.git` is exactly that: a file pointing at
    `<object store>/worktrees/<name>`, outside the mount, which git reports
    as `fatal: not a git repository: (null)` when it cannot follow. Mounting
    the object store back at the path the pointer names restores it.

    The source and the target are the same absolute path because the
    pointer is absolute once the store is mounted where the host has it,
    and because the option is a docker volume argument: `-v a:b:c` with the
    arguments quoted. Unquoted, docker splits the option on whitespace and
    rejects a path with a space in it, which act would report as a failure
    of the whole run.

    None when there is nothing to mount, which is the ordinary checkout: its
    `.git` directory is inside the bind mount already, and mounting the
    store over itself would only shadow it.
    """
    common_dir = _git_common_dir(repository)
    if common_dir is None or common_dir.is_relative_to(repository):
        return None
    # The container may read the object store and must not write it: the
    # repository root is bind-mounted read-write and is the only mount a
    # step needs to write. The quotes around each side are what makes the
    # option survive a path with a space in it -- docker hands the option
    # to the shell, which splits it on whitespace before docker parses it.
    source = str(common_dir)
    return f'-v "{source}":"{source}":ro'


def _platform_images() -> list[str]:
    """Return the `-P` entries mapping every Linux label to its image.

    act takes one image per platform, and prints `Skipping unsupported
    platform` -- with a successful exit and no step run -- for a workflow job
    whose label is missing here. Mapping every label in `_LINUX_PLATFORMS`
    gives that failure no way back in: the label vocabulary and the image map
    are read from the same set, so a label cannot be added to one without the
    other.

    The list is built here rather than written out so that act receives the
    same entries whichever label the workflow names.
    """
    return [f"{label}={_ACT_IMAGE}" for label in sorted(_LINUX_PLATFORMS)]


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
    ]
    for image in _platform_images():
        args.extend(["-P", image])
    git_mount = _git_common_dir_mount(_REPOSITORY_ROOT)
    if git_mount is not None:
        args.extend(["--container-options", git_mount])
    args.extend(
        [
            "--artifact-server-path",
            str(invocation.artefact_dir),
            "--json",
            "-b",
        ]
    )
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
        Execution configuration including artefact directory, event path,
        environment variables, and timeout.

    Returns
    -------
    tuple[int, str]
        Exit code and combined stdout/stderr logs.
    """
    config.artefact_dir.mkdir(parents=True, exist_ok=True)

    event_path = _resolve_event_path(config, event)

    run_env = os.environ.copy()
    run_env.update(_get_act_runtime_status().env)
    if config.env:
        run_env.update(config.env)

    container_env = _build_container_env(config, run_env)
    # Refuse an image-less case before act is invoked; see the function's
    # docstring for why the failure is silent when act skips a platform.
    _require_an_image_for(workflow, job)
    invocation = ActInvocation(
        workflow=workflow,
        event=event,
        job=job,
        event_path=event_path,
        artefact_dir=config.artefact_dir,
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
