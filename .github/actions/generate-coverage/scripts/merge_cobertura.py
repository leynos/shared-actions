#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Merge Cobertura XML files from Rust and Python coverage runs."""

from __future__ import annotations

from pathlib import Path
import typing as typ

from cmd_utils_loader import run_cmd
from common import _required_env
from plumbum.cmd import uvx
from plumbum.commands.processes import ProcessExecutionError
import os
import typer


def main(
    rust_file: typ.Annotated[
        Path | None,
        typer.Option(
            envvar="RUST_FILE",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
    python_file: typ.Annotated[
        Path | None,
        typer.Option(
            envvar="PYTHON_FILE",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
    output_path: typ.Annotated[Path | None, typer.Option(envvar="OUTPUT_PATH")] = None,
) -> None:
    """Merge two cobertura XML files and delete the inputs."""
    rust_file = rust_file or Path(_required_env("RUST_FILE"))
    python_file = python_file or Path(_required_env("PYTHON_FILE"))
    output_path = output_path or Path(_required_env("OUTPUT_PATH"))
    try:
        cmd = uvx["merge-cobertura", str(rust_file), str(python_file)]
        output = run_cmd(cmd)
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


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    typer.echo(f"Missing required environment variable: {name}", err=True)
    raise typer.Exit(2)
