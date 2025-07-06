#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Run Python coverage analysis using slipcover and pytest."""

from pathlib import Path
import xml.etree.ElementTree as ET

import typer
from plumbum import FG
from plumbum.cmd import python
from plumbum.commands.base import BoundCommand
from plumbum.commands.processes import ProcessExecutionError

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def coverage_cmd_for_fmt(fmt: str, out: Path) -> BoundCommand:
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


def percent_from_xml(xml_file: Path) -> str:
    """Return the total line coverage percentage from a cobertura XML file."""
    try:
        root = ET.parse(xml_file).getroot()
        rate = float(root.attrib["line-rate"])
    except Exception as exc:  # parse errors or missing attributes
        typer.echo(f"Failed to parse coverage XML: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return f"{rate * 100:.2f}"


def main(
    output_path: Path = OUTPUT_PATH_OPT,
    lang: str = LANG_OPT,
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
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
        xml_tmp = out.with_suffix(".xml")
        try:
            python["-m", "coverage", "xml", "-o", str(xml_tmp)]()
        except ProcessExecutionError as exc:
            typer.echo(
                f"coverage xml failed with code {exc.retcode}: {exc.stderr}", err=True
            )
            raise typer.Exit(code=exc.retcode or 1) from exc
        percent = percent_from_xml(xml_tmp)
        xml_tmp.unlink()
        Path(".coverage").replace(out)
    else:
        percent = percent_from_xml(out)

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
