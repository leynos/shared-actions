#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Compare current coverage with baseline and update if needed.

A baseline at or below the ratchet floor is a strict ratchet: any drop fails
and the baseline follows the current figure. A baseline above the floor only
requires the current figure to reach the floor; the baseline tracks the current
figure, with a warning when it drops by more than ``WARN_DROP_PP``. A floor of 0
switches the floor off, leaving the strict ratchet for every baseline.
"""

from __future__ import annotations

import math
from pathlib import Path

import typer

# A drop larger than this, from a baseline above the floor, raises a warning.
WARN_DROP_PP = 1.0

# Default ratchet floor, in percent. Zero disables the floor.
DEFAULT_RATCHET_FLOOR = 85.0


def read_baseline(file: Path) -> float:
    """Return the stored baseline coverage or 0.0 if missing/invalid."""
    if file.is_file():
        try:
            return float(file.read_text().strip())
        except ValueError:
            return 0.0
    return 0.0


def validate_floor(floor: float) -> float:
    """Return ``floor`` if it is a number from 0 to 100, else refuse it."""
    if not math.isfinite(floor) or not 0 <= floor <= 100:
        message = f"ratchet floor must be a number from 0 to 100, got {floor}"
        raise typer.BadParameter(message)
    return floor


def floor_mode(baseline: float, floor: float) -> bool:
    """Return whether the floor, rather than the strict ratchet, judges the run."""
    return floor > 0 and baseline > floor


BASELINE_FILE_OPT = typer.Option(
    Path(".coverage-baseline"), envvar="INPUT_BASELINE_FILE"
)
CURRENT_OPT = typer.Option(..., envvar="CURRENT_PERCENT")
FLOOR_OPT = typer.Option(DEFAULT_RATCHET_FLOOR, envvar="INPUT_RATCHET_FLOOR")
LANGUAGE_OPT = typer.Option("", envvar="INPUT_LANGUAGE")


def main(
    baseline_file: Path = BASELINE_FILE_OPT,
    *,
    current: float = CURRENT_OPT,
    floor: float = FLOOR_OPT,
    language: str = LANGUAGE_OPT,
) -> None:
    """Compare ``current`` coverage with the stored baseline and update it."""
    floor = validate_floor(floor)
    baseline = round(read_baseline(baseline_file), 2)
    current = round(current, 2)

    typer.echo(f"Current coverage: {current}%")
    typer.echo(f"Baseline coverage: {baseline}%")
    typer.echo(f"Ratchet floor: {floor:g}%")

    limit = floor if floor_mode(baseline, floor) else baseline
    if current < limit:
        typer.echo("Coverage decreased", err=True)
        raise typer.Exit(code=1)

    if floor_mode(baseline, floor) and current < baseline - WARN_DROP_PP:
        label = f" ({language})" if language else ""
        typer.echo(
            f"::warning title=Coverage dropped{label}::coverage fell from "
            f"{baseline}% to {current}%, still at or above the {floor:g}% floor"
        )

    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(f"{current:.2f}")


if __name__ == "__main__":
    typer.run(main)
