#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError


def main() -> None:
    """Install cargo-llvm-cov via cargo install command."""
    try:
        cargo["install", "cargo-llvm-cov"]()
        typer.echo("cargo-llvm-cov installed successfully")
    except ProcessExecutionError as exc:
        typer.echo(
            f"cargo install failed with code {exc.retcode}: {exc.stderr}", err=True
        )
        raise typer.Exit(code=exc.retcode or 1) from exc


if __name__ == "__main__":
    typer.run(main)
