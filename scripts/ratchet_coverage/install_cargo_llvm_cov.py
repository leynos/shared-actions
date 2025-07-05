#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from plumbum.cmd import cargo
import typer


def main() -> None:
    try:
        cargo["install", "cargo-llvm-cov"]()
        typer.echo("cargo-llvm-cov installed successfully")
    except Exception as e:
        typer.echo(f"Failed to install cargo-llvm-cov: {e}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
