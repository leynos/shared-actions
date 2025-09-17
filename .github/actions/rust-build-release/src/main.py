#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer", "packaging", "plumbum"]
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
from packaging import version as pkg_version
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils import run_cmd


def _cross_toolchain_arg(toolchain: str) -> str:
    """Return a cross-compatible ``+toolchain`` argument."""
    parts = toolchain.split("-")
    if len(parts) <= 1:
        return toolchain

    base_end = 1
    if len(parts) >= 4 and all(part.isdigit() for part in parts[1:4]):
        base_end = 4
    elif parts[1].isdigit():
        base_end = 2

    if len(parts) > base_end:
        sanitized = "-".join(parts[:base_end])
        return sanitized or toolchain
    return toolchain


app = typer.Typer(add_completion=False)


@app.command()
def main(
    target: str = typer.Argument("", help="Target triple to build"),
    toolchain: str = typer.Option(
        "1.89.0", envvar="RBR_TOOLCHAIN", help="Rust toolchain version"
    ),
) -> None:
    """Build the project for *target* using *toolchain*.

    Parameters
    ----------
    target : str
        Rust target triple to compile. When empty, the value falls back to the
        ``RBR_TARGET`` environment variable.
    toolchain : str
        Rust toolchain channel (for example ``"1.89.0"``) used when invoking
        ``cargo`` or ``cross``.

    Returns
    -------
    None
        Exits the process with a non-zero status when prerequisites are
        missing or compilation fails.

    Examples
    --------
    Run the command line interface to build a Linux binary:

    >>> # from a shell
    >>> uv run src/main.py x86_64-unknown-linux-gnu
    """
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

    rustup = shutil.which("rustup")
    if rustup is None:
        typer.echo("::error:: rustup not found", err=True)
        raise typer.Exit(1)
    result = subprocess.run(  # noqa: S603
        [rustup, "toolchain", "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    installed = result.stdout.splitlines()
    toolchain_channel = toolchain.split("-", 1)[0]
    toolchain_spec = toolchain if "-" in toolchain else toolchain_channel
    if "-" in toolchain and len(toolchain.split("-")) > 1:
        if not any(toolchain in line for line in installed):
            typer.echo(f"::error:: toolchain '{toolchain}' is not installed", err=True)
            raise typer.Exit(1)
    elif not any(line.startswith(f"{toolchain_channel}-") for line in installed):
        typer.echo(f"::error:: toolchain '{toolchain}' is not installed", err=True)
        raise typer.Exit(1)

    container_available = (
        shutil.which("docker") is not None or shutil.which("podman") is not None
    )
    engine = os.environ.get("CROSS_CONTAINER_ENGINE")
    if engine and shutil.which(engine) is None:
        msg = (
            "::warning:: "
            f"CROSS_CONTAINER_ENGINE={engine} specified but not found; "
            "disabling container use"
        )
        typer.echo(msg, err=True)
        container_available = False

    # Determine cross availability and version

    def get_cross_version(path: str) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603
                [path, "--version"],
                capture_output=True,
                check=True,
                text=True,
            )
            version_line = result.stdout.strip().split("\n")[0]
            if version_line.startswith("cross "):
                return version_line.split(" ")[1]
        except (OSError, subprocess.SubprocessError):
            return None

    required_cross_version = "0.2.5"

    cross_path = shutil.which("cross")
    cross_version = get_cross_version(cross_path) if cross_path else None

    def version_compare(installed: str, required: str) -> bool:
        return pkg_version.parse(installed) >= pkg_version.parse(required)

    if container_available:
        if cross_path is None or not version_compare(
            cross_version or "0", required_cross_version
        ):
            if cross_path is None:
                typer.echo("Installing cross (not found)...")
            else:
                typer.echo(
                    "Upgrading cross (found version "
                    f"{cross_version}, required >= {required_cross_version})..."
                )
            cmd = local["cargo"][
                "install", "cross", "--git", "https://github.com/cross-rs/cross"
            ]
            run_cmd(cmd)
        else:
            typer.echo(f"Using cached cross ({cross_version})")
    else:
        if cross_path:
            typer.echo("Container runtime not detected; using existing cross")
        else:
            typer.echo("Container runtime not detected; cross not installed")

    cross_path = shutil.which("cross")
    cross_version = get_cross_version(cross_path) if cross_path else None

    use_cross = cross_path is not None and container_available
    if use_cross:
        typer.echo(f"Building with cross ({cross_version})")
    else:
        if cross_path is not None:
            typer.echo("cross found but container runtime missing; using cargo")
        else:
            typer.echo("cross not installed; using cargo")

    cross_toolchain = _cross_toolchain_arg(toolchain_spec)
    cmd = local["cross" if use_cross else "cargo"][
        f"+{cross_toolchain if use_cross else toolchain_spec}",
        "build",
        "--release",
        "--target",
        target,
    ]
    try:
        run_cmd(cmd)
    except ProcessExecutionError as exc:
        fallback_reason = None
        if use_cross:
            stderr_text = getattr(exc, "stderr", "") or ""
            if exc.retcode in {125, 126}:
                fallback_reason = "launch the container runtime"
            elif "could not get os and arch" in stderr_text:
                fallback_reason = "detect the container platform"
        if fallback_reason:
            typer.echo(f"cross failed to {fallback_reason}; falling back to cargo")
            fallback = local["cargo"][
                f"+{toolchain_spec}", "build", "--release", "--target", target
            ]
            run_cmd(fallback)
        else:
            raise


if __name__ == "__main__":
    app()
