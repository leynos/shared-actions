#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer"]
# ///
"""Append a short release summary for the workflow run."""

from __future__ import annotations

from pathlib import Path

import typer

TAG_OPTION = typer.Option(..., envvar="RESOLVED_TAG")
INDEX_OPTION = typer.Option("", envvar="INPUT_UV_INDEX")
ENV_OPTION = typer.Option("pypi", envvar="INPUT_ENVIRONMENT_NAME")
SUMMARY_OPTION = typer.Option(..., envvar="GITHUB_STEP_SUMMARY")


def main(
    tag: str = TAG_OPTION,
    index: str = INDEX_OPTION,
    environment_name: str = ENV_OPTION,
    summary_path: Path = SUMMARY_OPTION,
) -> None:
    index_label = index or "pypi (default)"
    with summary_path.open("a", encoding="utf-8") as fh:
        fh.write(f"Released tag: {tag}\n")
        fh.write(f"Publish index: {index_label}\n")
        fh.write(f"Environment: {environment_name}\n")


if __name__ == "__main__":
    typer.run(main)
