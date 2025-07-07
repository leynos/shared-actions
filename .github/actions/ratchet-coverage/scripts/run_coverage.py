#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
import re
import shlex
from pathlib import Path

import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError


def extract_percent(output: str) -> str:
    """Return the coverage percentage parsed from ``output`` text."""

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)%", output)
    if not match:
        typer.echo("Could not parse coverage percent", err=True)
        raise typer.Exit(code=1)
    return match[1]


def main(
    args: str = typer.Option("", envvar="INPUT_ARGS"),
    github_output: Path = typer.Option(..., envvar="GITHUB_OUTPUT"),
) -> None:
    """Run ``cargo llvm-cov`` and write the percent value to ``GITHUB_OUTPUT``."""

    cmd = cargo["llvm-cov", "--summary-only"]
    if args:
        cmd = cmd[shlex.split(args)]
    try:
        retcode, output, err = cmd.run(retcode=None)
    except ProcessExecutionError as exc:  # Should not happen but guard anyway
        retcode, output, err = exc.retcode, exc.stdout, exc.stderr
    if retcode != 0:
        typer.echo(f"cargo llvm-cov failed with code {retcode}: {err}", err=True)
        raise typer.Exit(code=retcode)
    typer.echo(output)
    percent = extract_percent(output)
    with github_output.open("a") as fh:
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
