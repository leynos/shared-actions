#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Run ``cargo llvm-cov`` and output the coverage percentage."""

from __future__ import annotations

import re
import shlex
from pathlib import Path  # noqa: TC003 - used at runtime

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


ARGS_OPT = typer.Option("", envvar="INPUT_ARGS")
OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def main(
    *,
    args: str = ARGS_OPT,
    github_output: Path = OUTPUT_OPT,
) -> None:
    """Run ``cargo llvm-cov`` and write the percent value to ``GITHUB_OUTPUT``."""
    cmd = cargo["llvm-cov", "--summary-only"]
    if args:
        cmd = cmd[shlex.split(args)]
    typer.echo(f"$ {shlex.join(cmd.formulate())}")
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
