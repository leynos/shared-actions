#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Write coverage output metadata for the caller workflow."""

from pathlib import Path

import typer

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def main(
    output_path: Path = OUTPUT_PATH_OPT,
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
) -> None:
    """Write final outputs to ``GITHUB_OUTPUT`` for the caller workflow."""
    with github_output.open("a") as fh:
        fh.write(f"file={output_path}\n")
        fh.write(f"format={fmt}\n")


if __name__ == "__main__":
    typer.run(main)
