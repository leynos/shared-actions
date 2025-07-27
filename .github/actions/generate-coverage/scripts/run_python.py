#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Python coverage analysis using slipcover and pytest."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003 - used at runtime
import contextlib
import typing as t
from pathlib import Path

import typer
from plumbum import FG
from plumbum.cmd import python
from plumbum.commands.processes import ProcessExecutionError

if t.TYPE_CHECKING:  # pragma: no cover - type hints only
    from plumbum.commands.base import BoundCommand

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



def get_line_coverage_percent_from_cobertura(xml_file: Path) -> str:
    """Return the overall line coverage percentage from a Cobertura XML file.

    Parameters
    ----------
    xml_file : Path
        Path to the coverage file to read.

    Returns
    -------
    str
        The coverage percentage with two decimal places.
    """
    from decimal import ROUND_HALF_UP, Decimal

    from lxml import etree

    root = etree.parse(str(xml_file)).getroot()

    def num_or_zero(expr: str) -> int:
        n = root.xpath(f"number({expr})")
        return 0 if n != n else int(n)

    def lines_from_detail() -> tuple[int, int]:
        total = int(root.xpath("count(//class/lines/line)"))
        covered = int(root.xpath("count(//class/lines/line[number(@hits) > 0])"))
        return covered, total

    covered, total = lines_from_detail()
    if total == 0:
        covered = num_or_zero("/coverage/@lines-covered")
        total = num_or_zero("/coverage/@lines-valid")

    if total == 0:
        return "0.00"

    percent = (
        Decimal(covered) / Decimal(total) * 100
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{percent}"


@contextlib.contextmanager
def tmp_coveragepy_xml(out: Path) -> cabc.Generator[Path]:
    """Generate a cobertura XML from coverage.py and clean up afterwards."""
    xml_tmp = out.with_suffix(".xml")
    try:
        python["-m", "coverage", "xml", "-o", str(xml_tmp)]()
    except ProcessExecutionError as exc:
        typer.echo(
            f"coverage xml failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise typer.Exit(code=exc.retcode or 1) from exc
    try:
        yield xml_tmp
    finally:
        xml_tmp.unlink(missing_ok=True)


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
        with tmp_coveragepy_xml(out) as xml_tmp:
            percent = get_line_coverage_percent_from_cobertura(xml_tmp)
        Path(".coverage").replace(out)
    else:
        percent = get_line_coverage_percent_from_cobertura(out)

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
