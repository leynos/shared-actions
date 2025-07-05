#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from plumbum.cmd import cargo
import typer


def main() -> None:
    cargo["install", "cargo-llvm-cov"]()


if __name__ == "__main__":
    typer.run(main)
