#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Copy OpenBSD standard library build artefacts into the nightly sysroot.

The copy is performed via ``rsync`` into a temporary directory and then
renamed into place so consumers never see a partially copied stdlib.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path  # noqa: TC003

import typer
from plumbum import local
from plumbum.commands.processes import CommandNotFound

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd

app = typer.Typer(add_completion=False)


@app.command()
def main(artefact_dir: Path, nightly_sysroot: Path) -> None:
    """Copy artefacts from *artefact_dir* into *nightly_sysroot*."""
    if not artefact_dir.is_dir():
        typer.echo(f"Error: Build artefacts not found at {artefact_dir}", err=True)
        raise typer.Exit(1)

    base = nightly_sysroot / "lib" / "rustlib"
    dest = base / "x86_64-unknown-openbsd"
    tmp = base / "x86_64-unknown-openbsd.new"

    if tmp.exists():
        shutil.rmtree(tmp)

    if os.name == "nt":
        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(artefact_dir, tmp)
    else:
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            run_cmd(local["rsync"]["-a", "--delete", f"{artefact_dir}/", str(tmp)])
        except CommandNotFound:
            # Fallback when rsync is not available.
            shutil.copytree(artefact_dir, tmp, dirs_exist_ok=True)

    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)

    typer.echo(f"Copied OpenBSD stdlib from {artefact_dir} to {dest}")


if __name__ == "__main__":
    app()
