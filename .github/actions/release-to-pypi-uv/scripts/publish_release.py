#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer"]
# ///
"""Publish the built distributions using uv."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cmd_utils import run_cmd  # noqa: E402

INDEX_OPTION = typer.Option("", envvar="INPUT_UV_INDEX")


def main(index: str = INDEX_OPTION) -> None:
    if index:
        typer.echo(f"Publishing with uv to index '{index}'")
        run_cmd(["uv", "publish", "--index", index])
    else:
        typer.echo("Publishing with uv to default index (PyPI)")
        run_cmd(["uv", "publish"])


if __name__ == "__main__":
    typer.run(main)
