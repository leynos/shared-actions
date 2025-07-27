"""Coverage parsing helpers."""

from __future__ import annotations

import typing as t
from decimal import ROUND_HALF_UP, Decimal

import typer
from lxml import etree

if t.TYPE_CHECKING:  # pragma: no cover - import for type hints only
    from pathlib import Path


def get_line_coverage_percent_from_cobertura(xml_file: Path) -> str:
    """Return the overall line coverage percentage from a Cobertura XML file.

    Parameters
    ----------
    xml_file : Path
        Path to the coverage file to read.

    Returns
    -------
    str
        The coverage percentage with two decimal places. ``"0.00"`` is returned
        if the file cannot be read or parsed.
    """
    try:
        root = etree.parse(str(xml_file)).getroot()
    except OSError as exc:
        typer.echo(f"Could not read {xml_file}: {exc}", err=True)
        return "0.00"
    except etree.LxmlError as exc:  # XMLSyntaxError plus related issues
        typer.echo(f"Failed to parse coverage XML {xml_file}: {exc}", err=True)
        return "0.00"

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

    percent = (Decimal(covered) / Decimal(total) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return f"{percent}"
