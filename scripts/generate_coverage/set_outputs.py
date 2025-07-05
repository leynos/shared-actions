#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
from pathlib import Path

import typer


def main(
    output_path: Path = typer.Option(..., envvar="INPUT_OUTPUT_PATH"),
    fmt: str = typer.Option(..., envvar="DETECTED_FMT"),
    github_output: Path = typer.Option(..., envvar="GITHUB_OUTPUT"),
) -> None:
    """Write final outputs to ``GITHUB_OUTPUT`` for the caller workflow."""
    with github_output.open("a") as fh:
        fh.write(f"file={output_path}\n")
        fh.write(f"format={fmt}\n")


if __name__ == "__main__":
    typer.run(main)
