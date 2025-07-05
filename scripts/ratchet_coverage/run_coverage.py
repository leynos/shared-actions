#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from pathlib import Path
import re
import shlex
import typer
from plumbum.cmd import cargo


def extract_percent(output: str) -> str:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)%", output)
    if not match:
        typer.echo("Could not parse coverage percent", err=True)
        raise typer.Exit(code=1)
    return match[1]


def main(
    args: str = typer.Option("", envvar="INPUT_ARGS"),
    github_output: Path = typer.Option(..., envvar="GITHUB_OUTPUT"),
) -> None:
    cmd = cargo["llvm-cov", "--summary-only"]
    if args:
        cmd = cmd[shlex.split(args)]
    output = cmd()
    typer.echo(output)
    percent = extract_percent(output)
    with github_output.open("a") as fh:
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
