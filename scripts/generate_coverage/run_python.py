#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from pathlib import Path

import typer
from plumbum import FG
from plumbum.cmd import python
from plumbum.commands.processes import ProcessExecutionError


def coverage_cmd_for_fmt(fmt: str, out: Path):
    """Return the slipcover command for the requested format."""
    if fmt == "cobertura":
        return python[
            "-m",
            "slipcover",
            "--branch",
            "--xml",
            str(out),
            "-m",
            "pytest",
            "-v",
        ]
    return python["-m", "slipcover", "--branch", "-m", "pytest", "-v"]


def main(
    output_path: Path = typer.Option(..., envvar="INPUT_OUTPUT_PATH"),
    lang: str = typer.Option(..., envvar="DETECTED_LANG"),
    fmt: str = typer.Option(..., envvar="DETECTED_FMT"),
    github_output: Path = typer.Option(..., envvar="GITHUB_OUTPUT"),
) -> None:
    """Run slipcover coverage and write the output path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.python{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = coverage_cmd_for_fmt(fmt, out)
    try:
        cmd & FG
    except ProcessExecutionError as exc:
        raise typer.Exit(code=exc.retcode or 1) from exc

    if fmt == "coveragepy":
        Path(".coverage").rename(out)

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")


if __name__ == "__main__":
    typer.run(main)
