#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Build a Rust project in release mode for a target triple."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[4]))

import typer

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

    container_available = shutil.which("docker") is not None or shutil.which(
        "podman"
    ) is not None
    if container_available and shutil.which("cross") is None:
        typer.echo("Installing cross...")
        run_cmd(
            [
                "cargo",
                "install",
                "cross",
                "--git",
                "https://github.com/cross-rs/cross",
            ]
        )

    cmd = [
        "cross" if container_available else "cargo",
        f"+{toolchain}",
        "build",
        "--release",
        "--target",
        target,
    ]
    run_cmd(cmd)


if __name__ == "__main__":
    app()
