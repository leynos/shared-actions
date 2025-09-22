#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer", "packaging"]
# ///
"""Build a Rust project in release mode for a target triple."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[4]))

import typer
from cross_manager import ensure_cross
from runtime import CROSS_CONTAINER_ERROR_CODES, runtime_available
from toolchain import configure_windows_linkers, read_default_toolchain
from utils import UnexpectedExecutableError, ensure_allowed_executable, run_validated

from cmd_utils import run_cmd

DEFAULT_TOOLCHAIN = read_default_toolchain()

WINDOWS_TARGET_SUFFIXES = (
    "-pc-windows-msvc",
    "-pc-windows-gnu",
    "-pc-windows-gnullvm",
    "-windows-msvc",
    "-windows-gnu",
    "-windows-gnullvm",
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
        capture_output=True,
        text=True,
        check=True,
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
    if not target:
        target = os.environ.get("RBR_TARGET", "")
    if not target:
        env_rbr_target = os.environ.get("RBR_TARGET", "<unset>")
        env_input_target = os.environ.get("INPUT_TARGET", "<unset>")
        env_github_ref = os.environ.get("GITHUB_REF", "<unset>")
        typer.echo(
            "::error:: no build target specified; "
            "set input 'target' or env RBR_TARGET\n"
            f"RBR_TARGET={env_rbr_target} "
            f"INPUT_TARGET={env_input_target} "
            f"GITHUB_REF={env_github_ref}",
            err=True,
        )
        raise typer.Exit(1)

    rustup_path = shutil.which("rustup")
    if rustup_path is None:
        typer.echo("::error:: rustup not found", err=True)
        raise typer.Exit(1)
    try:
        rustup_exec = ensure_allowed_executable(rustup_path, ("rustup", "rustup.exe"))
    except UnexpectedExecutableError:
        typer.echo("::error:: unexpected rustup executable", err=True)
        raise typer.Exit(1) from None
    installed_names = _list_installed_toolchains(rustup_exec)
    toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if not toolchain_name:
        try:
            run_cmd(
                [
                    rustup_exec,
                    "toolchain",
                    "install",
                    toolchain,
                    "--profile",
                    "minimal",
                    "--no-self-update",
                ]
            )
        except subprocess.CalledProcessError:
            typer.echo(
                f"::error:: failed to install toolchain '{toolchain}'",
                err=True,
            )
            typer.echo(
                f"::error:: requested toolchain '{toolchain}' not installed",
                err=True,
            )
            raise typer.Exit(1) from None
        installed_names = _list_installed_toolchains(rustup_exec)
        toolchain_name = _resolve_toolchain_name(toolchain, target, installed_names)
    if not toolchain_name:
        typer.echo(
            f"::error:: requested toolchain '{toolchain}' not installed",
            err=True,
        )
        raise typer.Exit(1)
    target_installed = True
    try:
        run_cmd([rustup_exec, "target", "add", "--toolchain", toolchain_name, target])
    except subprocess.CalledProcessError:
        typer.echo(
            f"::warning:: toolchain '{toolchain_name}' does not support "
            f"target '{target}'; continuing",
            err=True,
        )
        target_installed = False

    configure_windows_linkers(toolchain_name, target, rustup_exec)

    cross_path, cross_version = ensure_cross("0.2.5")
    docker_present = False
    podman_present = False
    if should_probe_container(sys.platform, target):
        docker_present = runtime_available("docker")
        podman_present = runtime_available("podman")
    has_container = docker_present or podman_present

    use_cross = cross_path is not None and has_container
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
                    [
                        rustup_exec,
                        "toolchain",
                        "install",
                        cross_toolchain_name,
                        "--profile",
                        "minimal",
                        "--no-self-update",
                    ]
                )
            except subprocess.CalledProcessError:
                typer.echo(
                    "::warning:: failed to install sanitized toolchain; using cargo",
                    err=True,
                )
                use_cross = False
            else:
                installed_names = _list_installed_toolchains(rustup_exec)
        if use_cross:
            cross_toolchain_spec = f"+{cross_toolchain_name}"

    if not use_cross and not target_installed:
        typer.echo(
            f"::error:: toolchain '{toolchain_name}' does not support "
            f"target '{target}'",
            err=True,
        )
        raise typer.Exit(1)

    if not use_cross:
        if cross_path is None:
            typer.echo("cross missing; using cargo")
        elif not has_container:
            typer.echo(
                f"cross ({cross_version}) requires a container runtime; using cargo "
                f"(docker={docker_present}, podman={podman_present})"
            )
    else:
        typer.echo(f"Building with cross ({cross_version})")

    build_cmd = [
        "cross" if use_cross else "cargo",
        cross_toolchain_spec if use_cross else cargo_toolchain_spec,
        "build",
        "--release",
        "--target",
        target,
    ]
    try:
        run_cmd(build_cmd)
    except subprocess.CalledProcessError as exc:
        if use_cross and exc.returncode in CROSS_CONTAINER_ERROR_CODES:
            typer.echo(
                "::warning:: cross failed to start a container; retrying with cargo",
                err=True,
            )
            fallback_cmd = [
                "cargo",
                cargo_toolchain_spec,
                "build",
                "--release",
                "--target",
                target,
            ]
            run_cmd(fallback_cmd)
        else:
            raise


if __name__ == "__main__":
    app()
