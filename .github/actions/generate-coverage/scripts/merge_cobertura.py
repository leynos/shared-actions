#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Merge Cobertura XML files from Rust and Python coverage runs."""

from pathlib import Path

import typer
from plumbum.cmd import uvx
from plumbum.commands.processes import ProcessExecutionError

RUST_FILE_OPT = typer.Option(
    ...,
    envvar="RUST_FILE",
    exists=True,
    file_okay=True,
    dir_okay=False,
)
PYTHON_FILE_OPT = typer.Option(
    ...,
    envvar="PYTHON_FILE",
    exists=True,
    file_okay=True,
    dir_okay=False,
)
OUTPUT_PATH_OPT = typer.Option(..., envvar="OUTPUT_PATH")


def main(
    rust_file: Path = RUST_FILE_OPT,
    python_file: Path = PYTHON_FILE_OPT,
    output_path: Path = OUTPUT_PATH_OPT,
) -> None:
    """Merge two cobertura XML files and delete the inputs."""
    try:
        output = uvx["merge-cobertura", str(rust_file), str(python_file)]()
    except ProcessExecutionError as exc:
        typer.echo(
            f"merge-cobertura failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise typer.Exit(code=exc.retcode or 1) from exc
    output_path.write_text(output)
    rust_file.unlink()
    python_file.unlink()


if __name__ == "__main__":
    typer.run(main)
