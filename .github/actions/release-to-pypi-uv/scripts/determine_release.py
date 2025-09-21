#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer"]
# ///
"""Resolve the release tag and semantic version for the current run."""

from __future__ import annotations

import os
import re
from pathlib import Path  # noqa: TC003  # used at runtime for Typer CLI types

import typer

TAG_OPTION = typer.Option(None, envvar="INPUT_TAG")
GITHUB_OUTPUT_OPTION = typer.Option(..., envvar="GITHUB_OUTPUT")


def _emit_outputs(dest: Path, tag: str, version: str) -> None:
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(f"tag={tag}\n")
        fh.write(f"version={version}\n")


def main(
    tag: str | None = TAG_OPTION, github_output: Path = GITHUB_OUTPUT_OPTION
) -> None:
    """Resolve the release tag for the workflow execution.

    Parameters
    ----------
    tag : str or None
        Optional release tag supplied via the action input or CLI argument.
    github_output : Path
        Destination file used to communicate outputs to GitHub Actions.

    Raises
    ------
    typer.Exit
        If a tag cannot be resolved or does not follow the ``vMAJOR.MINOR.PATCH``
        semantic versioning format.
    """
    ref_type = os.getenv("GITHUB_REF_TYPE", "")
    ref_name = os.getenv("GITHUB_REF_NAME", "")

    resolved_tag: str | None = None
    if ref_type == "tag" and ref_name:
        resolved_tag = ref_name
    elif tag:
        resolved_tag = tag

    if not resolved_tag:
        typer.echo(
            "::error::No tag was provided and this run is not on a tag ref.",
            err=True,
        )
        raise typer.Exit(1)

    if not re.fullmatch(r"v\d+\.\d+\.\d+", resolved_tag):
        typer.echo(
            f"::error::Tag must be a valid semantic version (e.g. v1.2.3), got '{resolved_tag}'.",
            err=True,
        )
        raise typer.Exit(1)

    version = resolved_tag.removeprefix("v")

    _emit_outputs(github_output, resolved_tag, version)
    typer.echo(f"Resolved release tag: {resolved_tag} (version: {version})")


if __name__ == "__main__":
    typer.run(main)
