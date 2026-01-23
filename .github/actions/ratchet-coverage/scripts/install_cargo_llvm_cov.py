#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install cargo-llvm-cov via cargo-binstall."""

from __future__ import annotations

import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd

# Keep CARGO_LLVM_COV_VERSION in sync with security audits; update as needed.
CARGO_LLVM_COV_VERSION = "0.6.24"


def install_cargo_llvm_cov() -> None:
    """Install cargo-llvm-cov using cargo-binstall."""
    try:
        cmd = cargo[
            "binstall",
            "cargo-llvm-cov",
            "--version",
            CARGO_LLVM_COV_VERSION,
            "--no-confirm",
            "--force",
        ]
        run_cmd(cmd)
        typer.echo("cargo-llvm-cov installed successfully")
    except ProcessExecutionError as exc:
        typer.echo(
            f"cargo binstall failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise typer.Exit(code=exc.retcode or 1) from exc


def main() -> None:
    """Install cargo-llvm-cov via cargo-binstall."""
    install_cargo_llvm_cov()


if __name__ == "__main__":
    typer.run(main)
