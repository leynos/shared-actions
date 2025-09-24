#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.17,<0.18"]
# ///
"""Append a short release summary for the workflow run."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  # used at runtime for Typer CLI types

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
    """Append release details to the GitHub step summary file.

    Parameters
    ----------
    tag : str
        Resolved release tag to report.
    index : str
        Optional package index identifier provided to the publish step.
    environment_name : str
        Name of the deployment environment associated with the release.
    summary_path : Path
        File path to ``GITHUB_STEP_SUMMARY`` that should receive the content.
    """
    index_label = index or "pypi (default)"
    heading = "## Release summary\n"
    lines = [
        f"- Released tag: {tag}\n",
        f"- Publish index: {index_label}\n",
        f"- Environment: {environment_name}\n",
    ]

    prefix = "\n" if summary_path.exists() and summary_path.stat().st_size > 0 else ""
    with summary_path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + heading)
        for line in lines:
            fh.write(line)


if __name__ == "__main__":
    typer.run(main)
