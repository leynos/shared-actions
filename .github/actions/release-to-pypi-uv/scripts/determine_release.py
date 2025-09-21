#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer"]
# ///
"""Resolve the release tag and semantic version for the current run."""

from __future__ import annotations

import os
import re
from pathlib import Path

import typer

TAG_OPTION = typer.Option(None, envvar="INPUT_TAG")
GITHUB_OUTPUT_OPTION = typer.Option(..., envvar="GITHUB_OUTPUT")


def _emit_outputs(dest: Path, tag: str, version: str) -> None:
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(f"tag={tag}\n")
        fh.write(f"version={version}\n")


def main(tag: str | None = TAG_OPTION, github_output: Path = GITHUB_OUTPUT_OPTION) -> None:
    """Resolve the release tag and write outputs for downstream steps.

    Parameters
    ----------
    tag : str | None
        Tag supplied via workflow input when the workflow is not running on a
        tag reference.
    github_output : Path
        Path to the ``GITHUB_OUTPUT`` file that receives the resolved values.

    Raises
    ------
    typer.Exit
        Raised when no tag can be determined or the tag is not SemVer
        compliant.
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
