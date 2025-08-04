#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Copy OpenBSD standard library build artifacts into the nightly sysroot.

The copy is performed via ``rsync`` into a temporary directory and then
renamed into place so consumers never see a partially copied stdlib.
"""

from __future__ import annotations

import shutil
from pathlib import Path  # noqa: TC003

import typer

from cmd_utils import run_cmd

app = typer.Typer(add_completion=False)


@app.command()
def main(artifact_dir: Path, nightly_sysroot: Path) -> None:
    """Copy artifacts from *artifact_dir* into *nightly_sysroot*."""
    if not artifact_dir.is_dir():
        typer.echo(f"Error: Build artifacts not found at {artifact_dir}", err=True)
        raise typer.Exit(1)

    base = nightly_sysroot / "lib" / "rustlib"
    dest = base / "x86_64-unknown-openbsd"
    tmp = base / "x86_64-unknown-openbsd.new"

    tmp.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete", f"{artifact_dir}/", str(tmp)]
    run_cmd(cmd)

    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)

    typer.echo(f"Copied OpenBSD stdlib from {artifact_dir} to {dest}")


if __name__ == "__main__":
    app()
