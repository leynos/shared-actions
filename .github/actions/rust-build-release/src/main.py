#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging", "plumbum", "syspath-hack>=0.2,<0.4", "typer"]
# ///
"""Build a Rust project in release mode for a target triple."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003
import os
import shutil
import sys
import typing as typ
from pathlib import Path

from syspath_hack import add_to_syspath

try:
    from syspath_hack import prepend_project_root  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - compat for older syspath-hack

    def prepend_project_root(sigil: str = "pyproject.toml") -> Path:
        """Fallback prepend when syspath_hack lacks prepend_project_root."""
        root = Path(__file__).resolve().parents[4]
        add_to_syspath(root)
        return root


def _prime_repo_root() -> None:
    """Ensure the repository root containing cmd_utils is importable."""
    try:
        prepend_project_root(sigil="cmd_utils_importer.py")
    except (OSError, RuntimeError, ImportError):  # pragma: no cover - fallback
        add_to_syspath(Path(__file__).resolve().parents[4])


_prime_repo_root()

import typer  # noqa: E402
from cross_manager import ensure_cross  # noqa: E402
from plumbum import local  # noqa: E402
from plumbum.commands.processes import (  # noqa: E402
    ProcessExecutionError,
    ProcessTimedOut,
)
from runtime import (  # noqa: E402
    CROSS_CONTAINER_ERROR_CODES,
    DEFAULT_HOST_TARGET,
    runtime_available,
)
from toolchain import configure_windows_linkers, read_default_toolchain  # noqa: E402
from utils import (  # noqa: E402
    UnexpectedExecutableError,
    ensure_allowed_executable,
    run_validated,
)

if typ.TYPE_CHECKING:
    import subprocess

    from cmd_utils import SupportsFormulate
from cmd_utils_importer import import_cmd_utils  # noqa: E402

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

_TRIPLE_OS_COMPONENTS = {
    "linux",
    "windows",
    "darwin",
    "freebsd",
    "netbsd",
    "openbsd",
    "dragonfly",
    "solaris",
    "android",
    "ios",
    "emscripten",
    "haiku",
    "hermit",
    "fuchsia",
    "wasi",
    "redox",
    "illumos",
    "uefi",
    "macabi",
    "rumprun",
    "vita",
    "psp",
}

app = typer.Typer(add_completion=False)


class _CrossDecision(typ.NamedTuple):
    """Capture how the build should invoke cargo or cross."""

    cross_path: str | None
    cross_version: str | None
    use_cross: bool
    cross_toolchain_spec: str
    cargo_toolchain_spec: str
    use_cross_local_backend: bool
    docker_present: bool
    podman_present: bool
    has_container: bool
    container_engine: str | None
    requires_cross_container: bool


class _CommandWrapper:
    """Expose a stable display name for a plumbum command."""

    def __init__(self, command: SupportsFormulate, display_name: str) -> None:
        formulate_callable = getattr(command, "formulate", None)
        if not callable(formulate_callable):
            message = (
                f"{command!r} does not expose a callable formulate(); cannot wrap "
                "for display override"
            )
            raise TypeError(message)
        self._command: typ.Any = command
        self._display_name = display_name
        self._override_formulate: typ.Callable[[], cabc.Sequence[str]] | None = None

        def _override() -> list[str]:
            parts = list(formulate_callable())
            if parts:
                parts[0] = display_name
            return parts

        try:
            command.formulate = _override  # type: ignore[attr-defined]
            self._override_formulate = _override
        except (AttributeError, TypeError) as exc:
            typer.echo(
                f"::warning:: failed to set display override for {command!r}: {exc}",
                err=True,
            )
            self._override_formulate = None

    def formulate(self) -> cabc.Sequence[str]:
        formulate_callable = getattr(self._command, "formulate", None)
        if not callable(formulate_callable):
            typer.echo(
                f"::warning:: command {self._command!r} does not support formulate(); "
                "returning display name only",
                err=True,
            )
            return [self._display_name]
        try:
            parts = list(formulate_callable())
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - unexpected failure
            message = (
                "::warning:: failed to generate command line for "
                f"{self._command!r}: {exc}"
            )
            typer.echo(message, err=True)
            return [self._display_name]
        if parts:
            parts[0] = self._display_name
        return parts

    def __call__(self, *args: object, **kwargs: object) -> SupportsFormulate:
        return self._command(*args, **kwargs)

    def run(
        self, *args: object, **kwargs: object
    ) -> tuple[int, str | bytes | None, str | bytes | None]:
        return self._command.run(*args, **kwargs)

    def popen(self, *args: object, **kwargs: object) -> subprocess.Popen[typ.Any]:
        return self._command.popen(*args, **kwargs)

    def with_env(self, *args: object, **kwargs: object) -> _CommandWrapper:
        wrapped = self._command.with_env(*args, **kwargs)
        wrapped_formulate = getattr(wrapped, "formulate", None)
        if not callable(wrapped_formulate):
            message = (
                f"{wrapped!r} returned from with_env() does not expose formulate(); "
                "cannot maintain display override"
            )
            raise TypeError(message)
        return _CommandWrapper(wrapped, self._display_name)

    def __getattr__(self, name: str) -> object:
        return getattr(self._command, name)


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


def _resolve_toolchain_name(
    toolchain: str, target: str, installed_names: list[str]
) -> str:
    """Choose the best matching installed toolchain for *toolchain*."""
    preferred = (f"{toolchain}-{target}", toolchain)
    for name in installed_names:
        if name in preferred:
            return name
    channel_prefix = f"{toolchain}-"
    for name in installed_names:
        if name == toolchain or name.startswith(channel_prefix):
            return name
    return ""


def _looks_like_triple(candidate: str) -> bool:
    """Return ``True`` when *candidate* resembles a target triple."""
    components = [part for part in candidate.split("-") if part]
    if len(components) < 3:
        return False
    return any(component in _TRIPLE_OS_COMPONENTS for component in components[1:])


def _toolchain_channel(toolchain_name: str) -> str:
    """Strip any target triple suffix from *toolchain_name* for CLI overrides."""
    for suffix_parts in (4, 3):
        parts = toolchain_name.rsplit("-", suffix_parts)
        if len(parts) != suffix_parts + 1:
            continue
        candidate = "-".join(parts[-suffix_parts:])
        if _looks_like_triple(candidate):
            return parts[0]
    return toolchain_name


def _probe_runtime(name: str) -> bool:
    """Return True when *name* runtime is available, tolerating probe timeouts."""
    try:
        return runtime_available(name)
    except ProcessTimedOut as exc:
        timeout = getattr(exc, "timeout", None)
        duration = f" after {timeout}s" if timeout else ""
        message = (
            f"::warning::{name} runtime probe timed out{duration}; "
            "treating runtime as unavailable"
        )
        typer.echo(message, err=True)
        return False


def _emit_missing_target_error() -> typ.NoReturn:
    """Print an error describing the missing target configuration."""
    env_rbr_target = os.environ.get("RBR_TARGET", "<unset>")
    env_input_target = os.environ.get("INPUT_TARGET", "<unset>")
    env_github_ref = os.environ.get("GITHUB_REF", "<unset>")
    message = (
        "::error:: no build target specified; set input 'target' or env RBR_TARGET\n"
        f"RBR_TARGET={env_rbr_target} "
        f"INPUT_TARGET={env_input_target} "
        f"GITHUB_REF={env_github_ref}"
    )
    typer.echo(message, err=True)
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
        typer.echo("::error:: rustup not found", err=True)
        raise typer.Exit(1)
    try:
        return ensure_allowed_executable(rustup_path, ("rustup", "rustup.exe"))
    except UnexpectedExecutableError:
        typer.echo("::error:: unexpected rustup executable", err=True)
        raise typer.Exit(1) from None


def _fallback_toolchain_name(toolchain: str, installed_names: list[str]) -> str:
    """Return a toolchain matching *toolchain* or its channel prefix."""
    channel_prefix = f"{toolchain}-"
    return next(
        (
            name
            for name in installed_names
            if name == toolchain or name.startswith(channel_prefix)
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
        typer.echo(
            f"::error:: failed to install toolchain '{toolchain}'",
            err=True,
        )
        typer.echo(
            f"::error:: requested toolchain '{toolchain}' not installed",
            err=True,
        )
        raise typer.Exit(1) from None


def _resolve_toolchain(
    rustup_exec: str, toolchain: str, target: str
) -> tuple[str, list[str]]:
    """Return the installed toolchain to use for the build."""
    installed_names = _list_installed_toolchains(rustup_exec)
    toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if toolchain_name:
        return toolchain_name, installed_names

    _install_toolchain_channel(rustup_exec, toolchain)
    installed_names = _list_installed_toolchains(rustup_exec)
    toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if toolchain_name:
        return toolchain_name, installed_names

    toolchain_name = _fallback_toolchain_name(toolchain, installed_names)
    if toolchain_name:
        return toolchain_name, installed_names

    typer.echo(
        f"::error:: requested toolchain '{toolchain}' not installed",
        err=True,
    )
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
        typer.echo(
            f"::warning:: toolchain '{toolchain_name}' does not support "
            f"target '{target}'; continuing",
            err=True,
        )
        return False
    return True


def _decide_cross_usage(
    toolchain_name: str,
    installed_names: list[str],
    rustup_exec: str,
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
    cross_toolchain_spec = cargo_toolchain_spec

    if use_cross:
        cross_toolchain_name = _toolchain_channel(toolchain_name)
        if (
            cross_toolchain_name != toolchain_name
            and cross_toolchain_name not in installed_names
        ):
            try:
                run_cmd(
                    local[rustup_exec][
                        "toolchain",
                        "install",
                        cross_toolchain_name,
                        "--profile",
                        "minimal",
                        "--no-self-update",
                    ]
                )
            except ProcessExecutionError:
                typer.echo(
                    "::warning:: failed to install sanitized toolchain; using cargo",
                    err=True,
                )
                use_cross = False
            else:
                installed_names = _list_installed_toolchains(rustup_exec)
        if use_cross:
            cross_toolchain_spec = f"+{cross_toolchain_name}"

    return _CrossDecision(
        cross_path=cross_path,
        cross_version=cross_version,
        use_cross=use_cross,
        cross_toolchain_spec=cross_toolchain_spec,
        cargo_toolchain_spec=cargo_toolchain_spec,
        use_cross_local_backend=use_cross_local_backend,
        docker_present=docker_present,
        podman_present=podman_present,
        has_container=has_container,
        container_engine=container_engine,
        requires_cross_container=requires_cross_container,
    )


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


def _build_cross_command(
    decision: _CrossDecision, target_to_build: str, manifest_path: Path
) -> SupportsFormulate:
    cross_executable = decision.cross_path or "cross"
    executor = local[cross_executable]
    build_cmd = executor[
        decision.cross_toolchain_spec,
        "build",
        "--manifest-path",
        str(manifest_path),
        "--release",
        "--target",
        target_to_build,
    ]
    if decision.cross_path:
        build_cmd = _CommandWrapper(build_cmd, Path(decision.cross_path).name)
    return build_cmd


def _build_cargo_command(
    cargo_toolchain_spec: str, target_to_build: str, manifest_path: Path
) -> SupportsFormulate:
    executor = local["cargo"]
    build_cmd = executor[
        cargo_toolchain_spec,
        "build",
        "--manifest-path",
        str(manifest_path),
        "--release",
        "--target",
        target_to_build,
    ]
    return _CommandWrapper(build_cmd, "cargo")


def _handle_cross_container_error(
    exc: ProcessExecutionError,
    decision: _CrossDecision,
    target_to_build: str,
    manifest_path: Path,
) -> None:
    if decision.use_cross and exc.retcode in CROSS_CONTAINER_ERROR_CODES:
        if decision.requires_cross_container and not decision.use_cross_local_backend:
            engine = decision.container_engine or "unknown"
            typer.echo(
                "::error:: cross failed to start a container runtime for "
                f"target '{target_to_build}' (engine={engine})",
                err=True,
            )
            raise typer.Exit(exc.retcode) from exc

        typer.echo(
            "::warning:: cross failed to start a container; retrying with cargo",
            err=True,
        )
        fallback_cmd = _build_cargo_command(
            decision.cargo_toolchain_spec,
            target_to_build,
            manifest_path,
        )
        run_cmd(fallback_cmd)
        return
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
        typer.echo(
            f"::error:: Cargo manifest not found at {manifest_location}",
            err=True,
        )
        raise typer.Exit(1)
    return manifest_location


def _manifest_argument(manifest_path: Path) -> Path:
    """Return a manifest path suitable for CLI consumption."""
    cwd = Path.cwd().resolve()
    try:
        return manifest_path.relative_to(cwd)
    except ValueError:
        return manifest_path


@app.command()
def main(
    target: str = typer.Argument("", help="Target triple to build"),
    toolchain: str = typer.Option(
        DEFAULT_TOOLCHAIN,
        envvar="RBR_TOOLCHAIN",
        help="Rust toolchain version",
    ),
) -> None:
    """Build the project for *target* using *toolchain*."""
    target_to_build = _resolve_target_argument(target)
    rustup_exec = _ensure_rustup_exec()
    toolchain_name, installed_names = _resolve_toolchain(
        rustup_exec, toolchain, target_to_build
    )
    target_installed = _ensure_target_installed(
        rustup_exec, toolchain_name, target_to_build
    )

    configure_windows_linkers(toolchain_name, target_to_build, rustup_exec)

    host_target = DEFAULT_HOST_TARGET
    decision = _decide_cross_usage(
        toolchain_name, installed_names, rustup_exec, target_to_build, host_target
    )

    if decision.requires_cross_container:
        if decision.use_cross_local_backend:
            typer.echo(
                "::error:: target "
                f"'{target_to_build}' requires cross with a container runtime "
                f"on host '{host_target}'; CROSS_NO_DOCKER=1 is unsupported when "
                "a container runtime is required",
                err=True,
            )
            raise typer.Exit(1)

        if not decision.use_cross:
            details: list[str] = []
            if decision.cross_path is None:
                details.append("cross is not installed")
            if not decision.has_container:
                details.append("no container runtime detected")
            detail_suffix = f", {', '.join(details)}" if details else ""
            typer.echo(
                "::error:: target "
                f"'{target_to_build}' requires cross with a container runtime "
                f"on host '{host_target}'"
                f"{detail_suffix}",
                err=True,
            )
            raise typer.Exit(1)

    if not target_installed and (
        not decision.use_cross or decision.use_cross_local_backend
    ):
        typer.echo(
            f"::error:: toolchain '{toolchain_name}' does not support "
            f"target '{target_to_build}'",
            err=True,
        )
        raise typer.Exit(1)

    _announce_build_mode(decision)

    previous_engine, applied_engine = _configure_cross_container_engine(decision)

    manifest_path = _resolve_manifest_path()
    manifest_argument = _manifest_argument(manifest_path)
    if decision.use_cross:
        build_cmd = _build_cross_command(decision, target_to_build, manifest_argument)
    else:
        build_cmd = _build_cargo_command(
            decision.cargo_toolchain_spec, target_to_build, manifest_argument
        )
    try:
        run_cmd(build_cmd)
    except ProcessExecutionError as exc:
        _handle_cross_container_error(exc, decision, target_to_build, manifest_argument)
    finally:
        _restore_container_engine(previous_engine, applied_engine=applied_engine)


if __name__ == "__main__":
    app()
