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
from packaging import version as pkg_version

from cmd_utils import run_cmd

app = typer.Typer(add_completion=False)


@app.command()
def main(
    target: str = typer.Argument("", help="Target triple to build"),
    toolchain: str = typer.Option(
        "1.89.0", envvar="RBR_TOOLCHAIN", help="Rust toolchain version"
    ),
) -> None:
    """Build the project for *target* using *toolchain*."""
    if not target:
        target = os.environ.get("RBR_TARGET", "")
    if not target:
        typer.echo("::error:: no build target specified", err=True)
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
    if not any(line.startswith(f"{toolchain}-") for line in installed):
        typer.echo(f"::error:: toolchain '{toolchain}' is not installed", err=True)
        raise typer.Exit(1)

    container_available = shutil.which("docker") is not None or shutil.which(
        "podman"
    ) is not None

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
            run_cmd(
                [
                    "cargo",
                    "install",
                    "cross",
                    "--git",
                    "https://github.com/cross-rs/cross",
                ]
            )
        else:
            typer.echo(f"Using cached cross ({cross_version})")
    else:
        if cross_path:
            typer.echo("Container runtime not detected; using existing cross")
        else:
            typer.echo("Container runtime not detected; cross not installed")

    cross_path = shutil.which("cross")
    cross_version = get_cross_version(cross_path) if cross_path else None

    use_cross = cross_path is not None
    if use_cross:
        if container_available:
            typer.echo(f"Building with cross ({cross_version})")
        else:
            typer.echo(
                "cross found but container runtime missing; attempting build with cross"
            )
    else:
        typer.echo("cross not installed; using cargo")

    cmd = [
        "cross" if use_cross else "cargo",
        f"+{toolchain}",
        "build",
        "--release",
        "--target",
        target,
    ]
    run_cmd(cmd)


if __name__ == "__main__":
    app()
