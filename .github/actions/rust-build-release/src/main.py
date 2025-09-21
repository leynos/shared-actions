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
    result = run_validated(
        rustup_exec,
        ["toolchain", "list"],
        allowed_names=("rustup", "rustup.exe"),
        capture_output=True,
        text=True,
        check=True,
    )
    installed = result.stdout.splitlines()
    installed_names = [line.split()[0] for line in installed if line.strip()]
    # Prefer an installed toolchain that matches the requested target triple.
    preferred = (f"{toolchain}-{target}", toolchain)
    toolchain_name = next(
        (name for name in installed_names if name in preferred),
        "",
    )
    if not toolchain_name:
        # Fallback: any installed variant that starts with the channel name.
        channel_prefix = f"{toolchain}-"
        toolchain_name = next(
            (
                name
                for name in installed_names
                if name == toolchain or name.startswith(channel_prefix)
            ),
            "",
        )
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
        else:
            typer.echo(
                f"cross ({cross_version}) requires a container runtime; using cargo "
                f"(docker={docker_present}, podman={podman_present})"
            )
    else:
        typer.echo(f"Building with cross ({cross_version})")

    toolchain_spec = (
        f"+{toolchain_name.rsplit('-', 4)[0]}" if use_cross else f"+{toolchain_name}"
    )
    build_cmd = [
        "cross" if use_cross else "cargo",
        toolchain_spec,
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
                f"+{toolchain_name}",
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
