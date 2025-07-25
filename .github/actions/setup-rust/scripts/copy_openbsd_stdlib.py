#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Copy OpenBSD standard library build artifacts into the nightly sysroot."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(artifact_dir: Path, nightly_sysroot: Path) -> None:
    """Copy artifacts from *artifact_dir* into *nightly_sysroot*."""
    if not artifact_dir.is_dir():
        typer.echo(f"Error: Build artifacts not found at {artifact_dir}", err=True)
        raise typer.Exit(1)

    dest = nightly_sysroot / "lib" / "rustlib" / "x86_64-unknown-openbsd"
    dest.mkdir(parents=True, exist_ok=True)

    for item in artifact_dir.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    typer.echo(f"Copied OpenBSD stdlib from {artifact_dir} to {dest}")


if __name__ == "__main__":
    app()
