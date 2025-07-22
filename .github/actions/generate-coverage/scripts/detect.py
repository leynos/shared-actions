#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Detect the project language and validate coverage format compatibility."""

from __future__ import annotations

import enum
import typing as t
from pathlib import Path

import click
import typer


class CoverageFmt(enum.StrEnum):
    """Supported coverage report formats."""

    LCOV = "lcov"
    COBERTURA = "cobertura"
    COVERAGEPY = "coveragepy"


class CoverageFmtParam(click.ParamType):
    """Custom parameter type for coverage format strings."""

    name = "format"

    def convert(
        self,
        value: str,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> CoverageFmt:  # type: ignore[override]
        """Convert the incoming value to a ``CoverageFmt`` enum."""
        try:
            return CoverageFmt(value.lower())
        except ValueError:
            self.fail(f"Unsupported format: {value}", param, ctx)



class Lang(enum.StrEnum):
    """Project languages supported by the action."""

    RUST = "rust"
    PYTHON = "python"
    MIXED = "mixed"


FMT_OPT = typer.Option(
    CoverageFmt.COBERTURA,
    envvar="INPUT_FORMAT",
    type=CoverageFmtParam(),
)
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def get_lang() -> Lang:
    """Detect whether the project is Rust, Python or mixed."""
    cargo = Path("Cargo.toml").is_file()
    python = Path("pyproject.toml").is_file()
    if cargo:
        return Lang.MIXED if python else Lang.RUST
    if python:
        return Lang.PYTHON
    typer.echo("Neither Cargo.toml nor pyproject.toml found", err=True)
    raise typer.Exit(code=1)


def main(
    fmt: t.Annotated[CoverageFmt, FMT_OPT],
    github_output: t.Annotated[Path, GITHUB_OUTPUT_OPT],
) -> None:
    """Detect the project language and write it plus the format to ``GITHUB_OUTPUT``."""
    lang = get_lang()

    match (lang, fmt):
        case (Lang.RUST, CoverageFmt.COVERAGEPY):
            typer.echo("coveragepy format only supported for Python projects", err=True)
            raise typer.Exit(code=1)
        case (Lang.PYTHON, CoverageFmt.LCOV):
            typer.echo("lcov format only supported for Rust projects", err=True)
            raise typer.Exit(code=1)
        case (Lang.MIXED, fmt_case) if fmt_case != CoverageFmt.COBERTURA:
            typer.echo("Mixed projects only support cobertura format", err=True)
            raise typer.Exit(code=1)

    with github_output.open("a") as fh:
        fh.write(f"lang={lang.value}\nfmt={fmt.value}\n")


if __name__ == "__main__":
    typer.run(main)
