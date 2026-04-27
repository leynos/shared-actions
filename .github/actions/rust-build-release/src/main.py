#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging", "plumbum", "syspath-hack>=0.4.0,<0.5.0", "typer"]
# ///
"""Build a Rust project in release mode for a target triple."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003
import os
import shlex
import shutil
import sys
import typing as typ
from pathlib import Path

from syspath_hack import prepend_project_root

_SCRIPT_DIR = Path(__file__).resolve().parent
prepend_project_root(sigil="cmd_utils_importer.py", start=_SCRIPT_DIR)

import typer
from cross_manager import ensure_cross
from gha import debug as gha_debug
from gha import error as gha_error
from gha import warning as gha_warning
from plumbum import local
from plumbum.commands.processes import (
    ProcessExecutionError,
    ProcessTimedOut,
)
from runtime import (
    CROSS_CONTAINER_ERROR_CODES,
    DEFAULT_HOST_TARGET,
    runtime_available,
)
from toolchain import (
    configure_windows_linkers,
    read_default_toolchain,
    resolve_requested_toolchain,
)
from utils import (
    UnexpectedExecutableError,
    ensure_allowed_executable,
    run_validated,
)

if typ.TYPE_CHECKING:
    import subprocess

    from cmd_utils import SupportsFormulate

    class _SupportsEnvFormulate(SupportsFormulate, typ.Protocol):
        """Protocol for commands that support temporary environment bindings."""

        def with_env(self, *args: object, **kwargs: object) -> _SupportsEnvFormulate:
            """Return a command wrapper with the provided environment overrides."""
            ...


from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd

DEFAULT_TOOLCHAIN = read_default_toolchain()

WINDOWS_TARGET_SUFFIXES = (
    "-pc-windows-msvc",
    "-pc-windows-gnu",
    "-pc-windows-gnullvm",
    "-windows-msvc",
    "-windows-gnu",
    "-windows-gnullvm",
)

BSD_TARGET_SUFFIXES = (
    "unknown-freebsd",
    "unknown-openbsd",
    "unknown-netbsd",
)

app = typer.Typer(add_completion=False)


class _CrossDecision(typ.NamedTuple):
    """Capture how the build should invoke cargo or cross."""

    cross_path: str | None
    cross_version: str | None
    use_cross: bool
    cargo_toolchain_spec: str
    use_cross_local_backend: bool
    docker_present: bool
    podman_present: bool
    has_container: bool
    container_engine: str | None
    requires_cross_container: bool


class _CommandWrapper:
    """Expose a stable display name for a plumbum command."""

    def __init__(
        self,
        command: SupportsFormulate,
        display_name: str,
    ) -> None:
        """Wrap *command* with *display_name*."""
        _validate_formulation(command, display_name)
        self._command: typ.Any = command
        self._display_name = display_name

    def formulate(self) -> cabc.Sequence[str]:
        """Return the command argv with the configured display name applied."""
        formulate_callable = getattr(self._command, "formulate", None)
        if not callable(formulate_callable):
            return [self._display_name]
        try:
            parts = list(formulate_callable())
        except Exception:  # noqa: BLE001  # pragma: no cover - unexpected failure
            return [self._display_name]
        if parts:
            parts[0] = self._display_name
        return parts

    def __str__(self) -> str:
        """Return a shell-escaped display string for the wrapped command."""
        parts = [str(part) for part in self.formulate()]
        return shlex.join(parts)

    def __call__(self, *args: object, **kwargs: object) -> SupportsFormulate:
        """Delegate command invocation to the wrapped command."""
        return self._command(*args, **kwargs)

    def run(
        self, *args: object, **kwargs: object
    ) -> tuple[int, str | bytes | None, str | bytes | None]:
        """Run the wrapped command and return plumbum's result tuple."""
        return self._command.run(*args, **kwargs)

    def popen(self, *args: object, **kwargs: object) -> subprocess.Popen[typ.Any]:
        """Start the wrapped command as a subprocess."""
        return self._command.popen(*args, **kwargs)

    def with_env(self, *args: object, **kwargs: object) -> _CommandWrapper:
        """Return a wrapper around the command with temporary environment values."""
        wrapped = self._command.with_env(*args, **kwargs)
        _validate_formulation(wrapped, self._display_name)
        return _CommandWrapper(wrapped, self._display_name)

    def __getattr__(self, name: str) -> object:
        """Delegate unknown attributes to the wrapped command."""
        return getattr(self._command, name)


def _validate_formulation(command: SupportsFormulate, display_name: str) -> None:
    """Raise TypeError when *command* does not expose a callable formulate()."""
    formulate_callable = getattr(command, "formulate", None)
    if not callable(formulate_callable):
        message = (
            f"{command!r} does not expose a callable formulate(); "
            f"cannot wrap '{display_name}' for display override"
        )
        raise TypeError(message)


def _target_is_windows(target: str) -> bool:
    """Return True if *target* resolves to a Windows triple."""
    normalized = target.strip().lower()
    return any(normalized.endswith(suffix) for suffix in WINDOWS_TARGET_SUFFIXES)


def should_probe_container(host_platform: str, target: str) -> bool:
    """Determine whether container runtimes should be probed."""
    if host_platform != "win32":
        return True
    return not _target_is_windows(target)


def _list_installed_toolchains(rustup_exec: str) -> list[str]:
    """Return installed rustup toolchain names."""
    result = run_validated(
        rustup_exec,
        ["toolchain", "list"],
        allowed_names=("rustup", "rustup.exe"),
        method="run",
    )
    installed = result.stdout.splitlines()
    return [line.split()[0] for line in installed if line.strip()]


def _matches_toolchain_channel(name: str, toolchain: str) -> bool:
    """Return True if *name* matches *toolchain* exactly or by channel/dotted prefix."""
    channel_prefix = f"{toolchain}-"
    dotted_prefix = f"{toolchain}."
    return name == toolchain or name.startswith((channel_prefix, dotted_prefix))


def _resolve_toolchain_name(
    toolchain: str, target: str, installed_names: list[str]
) -> str:
    """Choose the best matching installed toolchain for *toolchain*."""
    preferred = (f"{toolchain}-{target}", toolchain)
    for name in installed_names:
        if name in preferred:
            return name
    for name in installed_names:
        if _matches_toolchain_channel(name, toolchain):
            return name
    return ""


def _probe_runtime(name: str) -> bool:
    """Return True when *name* runtime is available, tolerating probe timeouts."""
    try:
        return runtime_available(name)
    except ProcessTimedOut as exc:
        timeout = getattr(exc, "timeout", None)
        duration = f" after {timeout}s" if timeout else ""
        gha_warning(
            f"{name} runtime probe timed out{duration}; treating runtime as unavailable"
        )
        return False


def _emit_missing_target_error() -> typ.NoReturn:
    """Print an error describing the missing target configuration."""
    env_rbr_target = os.environ.get("RBR_TARGET", "<unset>")
    env_input_target = os.environ.get("INPUT_TARGET", "<unset>")
    env_github_ref = os.environ.get("GITHUB_REF", "<unset>")
    message = (
        "no build target specified; set input 'target' or env RBR_TARGET\n"
        f"RBR_TARGET={env_rbr_target} "
        f"INPUT_TARGET={env_input_target} "
        f"GITHUB_REF={env_github_ref}"
    )
    gha_error(message)
    raise typer.Exit(1)


def _resolve_target_argument(target: str) -> str:
    """Return the target to build, falling back to environment values."""
    if target:
        return target
    env_target = os.environ.get("RBR_TARGET", "")
    if env_target:
        return env_target
    _emit_missing_target_error()


def _ensure_rustup_exec() -> str:
    """Locate a trusted rustup executable or exit with an error."""
    rustup_path = shutil.which("rustup")
    if rustup_path is None:
        gha_error("rustup not found")
        raise typer.Exit(1)
    try:
        return ensure_allowed_executable(rustup_path, ("rustup", "rustup.exe"))
    except UnexpectedExecutableError:
        gha_error("unexpected rustup executable")
        raise typer.Exit(1) from None


def _fallback_toolchain_name(toolchain: str, installed_names: list[str]) -> str:
    """Return a toolchain matching *toolchain* or its channel prefix."""
    return next(
        (
            name
            for name in installed_names
            if _matches_toolchain_channel(name, toolchain)
        ),
        "",
    )


def _install_toolchain_channel(rustup_exec: str, toolchain: str) -> None:
    """Install the requested *toolchain* channel via rustup."""
    try:
        run_cmd(
            local[rustup_exec][
                "toolchain",
                "install",
                toolchain,
                "--profile",
                "minimal",
                "--no-self-update",
            ]
        )
    except ProcessExecutionError:
        gha_error(f"failed to install toolchain '{toolchain}'")
        gha_error(f"requested toolchain '{toolchain}' not installed")
        raise typer.Exit(1) from None


def _resolve_toolchain(rustup_exec: str, toolchain: str, target: str) -> str:
    """Return the installed toolchain to use for the build."""
    installed_names = _list_installed_toolchains(rustup_exec)
    toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if toolchain_name:
        return toolchain_name

    _install_toolchain_channel(rustup_exec, toolchain)
    installed_names = _list_installed_toolchains(rustup_exec)
    toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if toolchain_name:
        return toolchain_name

    toolchain_name = _fallback_toolchain_name(toolchain, installed_names)
    if toolchain_name:
        return toolchain_name

    gha_error(f"requested toolchain '{toolchain}' not installed")
    raise typer.Exit(1)


def _ensure_target_installed(
    rustup_exec: str, toolchain_name: str, target: str
) -> bool:
    """Attempt to install *target* for *toolchain_name*, returning success."""
    try:
        run_cmd(
            local[rustup_exec][
                "target",
                "add",
                "--toolchain",
                toolchain_name,
                target,
            ]
        )
    except ProcessExecutionError:
        gha_warning(
            f"toolchain '{toolchain_name}' does not support target '{target}'; "
            "continuing"
        )
        return False
    return True


def _decide_cross_usage(
    toolchain_name: str,
    target: str,
    host_target: str,
) -> _CrossDecision:
    """Return how cross should be used for the build."""
    cross_path, cross_version = ensure_cross("0.2.5")
    target_normalized = target.strip().lower()
    host_normalized = host_target.strip().lower()
    requires_cross_container = False
    for suffix in BSD_TARGET_SUFFIXES:
        if target_normalized.endswith(suffix):
            requires_cross_container = not host_normalized.endswith(suffix)
            break
    docker_present = False
    podman_present = False
    if should_probe_container(sys.platform, target):
        docker_present = _probe_runtime("docker")
        podman_present = _probe_runtime("podman")
    has_container = docker_present or podman_present
    container_engine: str | None = None
    if docker_present:
        container_engine = "docker"
    elif podman_present:
        container_engine = "podman"

    use_cross_local_backend = (
        os.environ.get("CROSS_NO_DOCKER") == "1" and sys.platform == "win32"
    )
    use_cross = cross_path is not None and (has_container or use_cross_local_backend)

    cargo_toolchain_spec = f"+{toolchain_name}"

    return _CrossDecision(
        cross_path=cross_path,
        cross_version=cross_version,
        use_cross=use_cross,
        cargo_toolchain_spec=cargo_toolchain_spec,
        use_cross_local_backend=use_cross_local_backend,
        docker_present=docker_present,
        podman_present=podman_present,
        has_container=has_container,
        container_engine=container_engine,
        requires_cross_container=requires_cross_container,
    )


def _validate_cross_requirements(
    decision: _CrossDecision, target_to_build: str, host_target: str
) -> None:
    """Validate cross-container requirements and exit if unsatisfied."""
    if not decision.requires_cross_container:
        return

    if decision.use_cross_local_backend:
        gha_error(
            "target "
            f"'{target_to_build}' requires cross with a container runtime "
            f"on host '{host_target}'; CROSS_NO_DOCKER=1 is unsupported when "
            "a container runtime is required"
        )
        raise typer.Exit(1)

    if not decision.use_cross:
        details: list[str] = []
        if decision.cross_path is None:
            details.append("cross is not installed")
        if not decision.has_container:
            details.append("no container runtime detected")
        detail_suffix = f", {', '.join(details)}" if details else ""
        gha_error(
            "target "
            f"'{target_to_build}' requires cross with a container runtime "
            f"on host '{host_target}'"
            f"{detail_suffix}"
        )
        raise typer.Exit(1)


def _announce_build_mode(decision: _CrossDecision) -> None:
    """Print how the build will proceed."""
    if decision.use_cross:
        if decision.use_cross_local_backend:
            typer.echo(
                f"Building with cross ({decision.cross_version}) using local backend "
                "(CROSS_NO_DOCKER=1)"
            )
        else:
            typer.echo(f"Building with cross ({decision.cross_version})")
        return

    if decision.cross_path is None:
        typer.echo("cross missing; using cargo")
        return

    if not decision.has_container and not decision.use_cross_local_backend:
        typer.echo(
            "cross ("
            f"{decision.cross_version}"
            ") requires a container runtime; using cargo "
            f"(docker={decision.docker_present}, podman={decision.podman_present})"
        )


def _configure_cross_container_engine(
    decision: _CrossDecision,
) -> tuple[str | None, str | None]:
    """Ensure CROSS_CONTAINER_ENGINE matches the active cross backend."""
    previous_engine = os.environ.get("CROSS_CONTAINER_ENGINE")

    if not decision.use_cross:
        return previous_engine, None

    if decision.use_cross_local_backend:
        return previous_engine, None

    if previous_engine is not None:
        return previous_engine, None

    engine = decision.container_engine
    if engine is None:
        return previous_engine, None

    os.environ["CROSS_CONTAINER_ENGINE"] = engine
    return previous_engine, engine


def _restore_container_engine(
    previous_engine: str | None, *, applied_engine: str | None
) -> None:
    """Restore CROSS_CONTAINER_ENGINE after a temporary cross configuration."""
    current_engine = os.environ.get("CROSS_CONTAINER_ENGINE")

    if previous_engine is None:
        if applied_engine is not None:
            # Always remove the variable when no prior value existed so callers
            # that temporarily enabled cross-backed containers do not leak the
            # setting across invocations—even if an unexpected code path mutated
            # the environment outside of ``_configure_cross_container_engine``.
            os.environ.pop("CROSS_CONTAINER_ENGINE", None)
        return

    if current_engine != previous_engine:
        os.environ["CROSS_CONTAINER_ENGINE"] = previous_engine


def _normalize_features(features: str) -> str:
    """Normalize comma-separated feature lists for --features arguments."""
    if not isinstance(features, str):
        return ""
    parts = [part.strip() for part in features.split(",")]
    normalized = [part for part in parts if part]
    return ",".join(normalized)


def _assert_cross_command_has_no_toolchain_override(cmd: cabc.Sequence[object]) -> None:
    """Raise ValueError if a cross argv contains a +toolchain override."""
    # Cross must not be given a +<toolchain>; rely on rust-toolchain.toml /
    # rustup override.
    offenders = [
        str(arg) for arg in cmd[1:] if isinstance(arg, str) and arg.startswith("+")
    ]
    if offenders:
        message = (
            "cross command must not include a +<toolchain> override; "
            f"found: {offenders!r}"
        )
        raise ValueError(message)


def _build_cross_command(
    decision: _CrossDecision, target_to_build: str, manifest_path: Path, features: str
) -> SupportsFormulate:
    """Build a cross command argv and validate it contains no +toolchain."""
    cross_executable = decision.cross_path or "cross"
    executor = local[cross_executable]
    cmd: list[object] = [
        cross_executable,
        "build",
        "--manifest-path",
        str(manifest_path),
        "--release",
        "--target",
        target_to_build,
    ]
    normalized_features = _normalize_features(features)
    if normalized_features:
        cmd.extend(["--features", normalized_features])
    _assert_cross_command_has_no_toolchain_override(cmd)
    build_cmd = executor[cmd[1:]]
    if decision.cross_path:
        build_cmd = _CommandWrapper(build_cmd, Path(decision.cross_path).name)
    return build_cmd


def _build_cargo_command(
    cargo_toolchain_spec: str, target_to_build: str, manifest_path: Path, features: str
) -> SupportsFormulate:
    """Build a cargo command argv, preserving any configured +toolchain."""
    executor = local["cargo"]
    cmd = [
        "build",
        "--manifest-path",
        str(manifest_path),
        "--release",
        "--target",
        target_to_build,
    ]
    normalized_features = _normalize_features(features)
    if normalized_features:
        cmd.extend(["--features", normalized_features])
    if cargo_toolchain_spec:
        cmd.insert(0, cargo_toolchain_spec)
    build_cmd = executor[cmd]
    wrapped_cmd = _CommandWrapper(build_cmd, "cargo")
    return wrapped_cmd  # noqa: RET504 - keep the named command for call-site logging.


def _handle_cross_container_error(
    exc: ProcessExecutionError,
    decision: _CrossDecision,
    target_to_build: str,
    manifest_path: Path,
    features: str,
) -> None:
    """Handle cross container startup failures or re-raise other errors."""
    if decision.use_cross and exc.retcode in CROSS_CONTAINER_ERROR_CODES:
        if decision.requires_cross_container and not decision.use_cross_local_backend:
            engine = decision.container_engine or "unknown"
            gha_error(
                "cross failed to start a container runtime for "
                f"target '{target_to_build}' (engine={engine})"
            )
            raise typer.Exit(exc.retcode) from exc

        gha_warning("cross failed to start a container; retrying with cargo")
        fallback_cmd = _build_cargo_command(
            decision.cargo_toolchain_spec,
            target_to_build,
            manifest_path,
            features,
        )
        gha_debug(f"fallback cargo argv: {fallback_cmd}")
        run_cmd(fallback_cmd)
        gha_debug(f"fallback cargo build completed for target '{target_to_build}'")
        return
    gha_error(
        f"cross build failed for target '{target_to_build}' (retcode={exc.retcode})"
    )
    raise exc


def _resolve_manifest_path() -> Path:
    """Locate the Cargo manifest for the project being built."""
    manifest_override = os.environ.get("RBR_MANIFEST_PATH", "").strip()
    manifest_argument = (
        Path(manifest_override).expanduser()
        if manifest_override
        else Path("Cargo.toml")
    )
    if manifest_argument.is_absolute():
        manifest_location = manifest_argument
    else:
        manifest_location = Path.cwd() / manifest_argument

    manifest_location = manifest_location.resolve()
    if not manifest_location.is_file():
        gha_error(f"Cargo manifest not found at {manifest_location}")
        raise typer.Exit(1)
    return manifest_location


def _manifest_argument(manifest_path: Path) -> Path:
    """Return a manifest path suitable for CLI consumption."""
    cwd = Path.cwd().resolve()
    try:
        return manifest_path.relative_to(cwd)
    except ValueError:
        return manifest_path


def _check_target_support(
    decision: _CrossDecision,
    toolchain_name: str,
    target_to_build: str,
    *,
    target_installed: bool,
) -> None:
    """Exit with an error if the toolchain cannot build the requested target."""
    if not target_installed and (
        not decision.use_cross or decision.use_cross_local_backend
    ):
        gha_error(
            f"toolchain '{toolchain_name}' does not support target '{target_to_build}'"
        )
        raise typer.Exit(1)


def _assemble_build_command(
    decision: _CrossDecision,
    target_to_build: str,
    manifest_argument: Path,
    features: str,
    explicit_toolchain: str,
) -> tuple[SupportsFormulate | None, str | None]:
    """Assemble the build command; return (cmd, None) or (None, error_message)."""
    if not decision.use_cross:
        cmd = _build_cargo_command(
            decision.cargo_toolchain_spec, target_to_build, manifest_argument, features
        )
        return cmd, None
    try:
        build_cmd = _build_cross_command(
            decision, target_to_build, manifest_argument, features
        )
    except ValueError as exc:
        return None, (
            f"cross command validation failed for target '{target_to_build}': {exc}"
        )
    if explicit_toolchain:
        build_cmd = typ.cast("_SupportsEnvFormulate", build_cmd).with_env(
            RUSTUP_TOOLCHAIN=explicit_toolchain
        )
    return build_cmd, None


@app.command()
def main(
    target: str = typer.Argument("", help="Target triple to build"),
    toolchain: str = typer.Option(
        "",
        envvar="RBR_TOOLCHAIN",
        help="Rust toolchain version override",
    ),
    features: str = typer.Option(
        "",
        envvar="RBR_FEATURES",
        help="Comma-separated list of Cargo features to enable",
    ),
) -> None:
    """Build the project for *target* using *toolchain*."""
    target_to_build = _resolve_target_argument(target)
    manifest_path = _resolve_manifest_path()
    explicit_toolchain = toolchain.strip()
    requested_toolchain = explicit_toolchain or resolve_requested_toolchain(
        explicit_toolchain,
        project_dir=Path.cwd(),
        manifest_path=manifest_path,
        fallback_toolchain=DEFAULT_TOOLCHAIN,
    )
    rustup_exec = _ensure_rustup_exec()
    toolchain_name = _resolve_toolchain(
        rustup_exec, requested_toolchain, target_to_build
    )
    target_installed = _ensure_target_installed(
        rustup_exec, toolchain_name, target_to_build
    )
    configure_windows_linkers(toolchain_name, target_to_build, rustup_exec)
    host_target = DEFAULT_HOST_TARGET
    decision = _decide_cross_usage(toolchain_name, target_to_build, host_target)
    _validate_cross_requirements(decision, target_to_build, host_target)
    _check_target_support(
        decision, toolchain_name, target_to_build, target_installed=target_installed
    )
    _announce_build_mode(decision)
    previous_engine, applied_engine = _configure_cross_container_engine(decision)
    try:
        manifest_argument = _manifest_argument(manifest_path)
        build_cmd, assemble_error = _assemble_build_command(
            decision,
            target_to_build,
            manifest_argument,
            features,
            explicit_toolchain,
        )
        if assemble_error is not None:
            gha_error(assemble_error)
            raise typer.Exit(1)
        if decision.use_cross:
            gha_debug(f"cross argv: {build_cmd}")
        else:
            gha_debug(f"cargo argv: {build_cmd}")
        run_cmd(build_cmd)
        gha_debug(f"build completed for target '{target_to_build}'")
    except ProcessExecutionError as exc:
        _handle_cross_container_error(
            exc, decision, target_to_build, manifest_argument, features
        )
    finally:
        _restore_container_engine(previous_engine, applied_engine=applied_engine)


if __name__ == "__main__":
    app()
