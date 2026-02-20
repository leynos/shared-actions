#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Detect the project language and validate coverage format compatibility."""

from __future__ import annotations

import enum
import os
from pathlib import Path

import typer


class CoverageFmt(enum.StrEnum):
    """Supported coverage report formats."""

    LCOV = "lcov"
    COBERTURA = "cobertura"
    COVERAGEPY = "coveragepy"


class Lang(enum.StrEnum):
    """Project languages supported by the action."""

    RUST = "rust"
    PYTHON = "python"
    MIXED = "mixed"


FMT_OPT = typer.Option(
    CoverageFmt.COBERTURA.value,
    envvar="INPUT_FORMAT",
    help="Coverage format: lcov, cobertura, or coveragepy",
)
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def _resolve_cargo_manifest(cargo_manifest: str) -> Path | None:
    """Resolve the selected Cargo manifest with root precedence."""
    root_manifest = Path("Cargo.toml")
    if root_manifest.is_file():
        return root_manifest

    configured_manifest = cargo_manifest.strip()
    if not configured_manifest:
        return None

    configured_path = Path(configured_manifest)
    if configured_path.is_file():
        return configured_path

    return None


def get_lang(cargo_manifest: str = "") -> tuple[Lang, Path | None]:
    """Detect project language and selected Cargo manifest (if any)."""
    selected_manifest = _resolve_cargo_manifest(cargo_manifest)
    python = Path("pyproject.toml").is_file()
    if selected_manifest is not None:
        return (Lang.MIXED if python else Lang.RUST), selected_manifest
    if python:
        return Lang.PYTHON, None
    typer.echo("Neither Cargo.toml nor pyproject.toml found", err=True)
    raise typer.Exit(code=1)


def main(
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
    cargo_manifest: str = "",
) -> None:
    """Detect the project language and write it plus the format to ``GITHUB_OUTPUT``."""
    if not cargo_manifest:
        cargo_manifest = os.getenv("INPUT_CARGO_MANIFEST", "")
    lang, selected_manifest = get_lang(cargo_manifest)
    try:
        fmt_enum = CoverageFmt(fmt.lower())
    except ValueError as exc:
        typer.echo(f"Unsupported format: {fmt}", err=True)
        raise typer.Exit(code=1) from exc

    match (lang, fmt_enum):
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
        fh.write(f"lang={lang.value}\nfmt={fmt_enum.value}\n")
        if selected_manifest is not None:
            fh.write(f"cargo_manifest={selected_manifest}\n")


if __name__ == "__main__":
    typer.run(main)
