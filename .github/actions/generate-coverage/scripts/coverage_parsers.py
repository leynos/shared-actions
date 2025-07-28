"""Coverage parsing helpers."""

from __future__ import annotations

import logging
import math
import re
import typing as t
from decimal import ROUND_HALF_UP, Decimal

import typer

try:  # runtime import for graceful fallback
    from lxml import etree
except ImportError as exc:  # pragma: no cover - fail fast if dependency missing
    typer.echo(
        "lxml is required for Cobertura parsing. Install with 'pip install lxml'.",
        err=True,
    )
    raise typer.Exit(1) from exc

logger = logging.getLogger(__name__)

# Match coverage.py's CLI output which rounds half up to two decimal places.
# ROUND_HALF_UP ensures we report the same values as the tool's summary.
QUANT = Decimal("0.01")

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
    except FileNotFoundError as exc:
        typer.echo(f"Coverage file not found: {xml_file}", err=True)
        raise typer.Exit(1) from exc
    except PermissionError as exc:
        typer.echo(f"Permission denied reading coverage file: {xml_file}", err=True)
        raise typer.Exit(1) from exc
    except etree.XMLSyntaxError as exc:
        typer.echo(f"Invalid XML in coverage file {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:  # pragma: no cover - unexpected failures
        typer.echo(f"Failed to parse coverage file {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc

    def num_or_zero(expr: str) -> int:
        try:
            n = root.xpath(f"number({expr})")
        except Exception:  # noqa: BLE001 - defensive
            return 0
        else:
            return 0 if math.isnan(n) else int(n)

    def lines_from_detail() -> tuple[int, int]:
        try:
            total = int(root.xpath("count(//class/lines/line)"))
            covered = int(root.xpath("count(//class/lines/line[number(@hits) > 0])"))
        except Exception:  # noqa: BLE001 - defensive
            return 0, 0
        else:
            return covered, total

    covered, total = lines_from_detail()
    if total == 0:
        covered = num_or_zero("/coverage/@lines-covered")
        total = num_or_zero("/coverage/@lines-valid")

    if total == 0:
        return "0.00"

    percent = (Decimal(covered) / Decimal(total) * 100).quantize(
        QUANT, rounding=ROUND_HALF_UP
    )
    return f"{percent}"


def get_line_coverage_percent_from_lcov(lcov_file: Path) -> str:
    """Return the overall line coverage percentage from an ``lcov.info`` file."""
    try:
        text = lcov_file.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Could not read {lcov_file}: {exc}", err=True)
        raise typer.Exit(1) from exc

    def total(tag: str) -> int:
        values = re.findall(rf"^{tag}:(\d+)$", text, flags=re.MULTILINE)
        try:
            return sum(int(v) for v in values)
        except ValueError as exc:
            typer.echo(f"Malformed lcov data in {lcov_file}: {exc}", err=True)
            raise typer.Exit(1) from exc

    lines_found = total("LF")
    lines_hit = total("LH")

    if lines_found == 0:
        logger.warning(
            "No lines found in lcov data. This may indicate an empty or "
            "misconfigured lcov file."
        )

    if lines_found == 0:
        return "0.00"

    percent = (Decimal(lines_hit) / Decimal(lines_found) * 100).quantize(
        QUANT, rounding=ROUND_HALF_UP
    )
    return f"{percent}"
