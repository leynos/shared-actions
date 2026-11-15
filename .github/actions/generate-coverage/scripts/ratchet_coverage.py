#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Compare current coverage with a baseline within a symmetric dead-band.

Coverage within +/-``RATCHET_TOLERANCE_PP`` percentage points of the stored
baseline is treated as noise: the run passes and the baseline is held. A drop
beyond the band fails the gate; a rise beyond the band advances the baseline.

A ratchet floor changes this for a baseline above the floor. There the gate
only requires the current figure to reach the floor, the baseline tracks the
current figure up or down, and a drop of more than the tolerance below the
baseline raises a warning rather than a failure. A floor of 0 switches the
floor off, so the dead-band rule applies to every baseline.
"""

from __future__ import annotations

import math
from pathlib import Path

import typer

# Provisional symmetric dead-band, in absolute percentage points, around the
# stored baseline. Coverage within +/- this many points of the baseline is
# treated as noise: the run passes and the baseline is left untouched. This
# guards against coverage nondeterminism (flaky per-run line counts) both from
# tripping a false "Coverage decreased" failure on a low run and from inflating
# the baseline on a lucky-high run (which would then make the next normal run
# fail). The baseline only advances on a genuine improvement beyond the band.
RATCHET_TOLERANCE_PP = 1.0

# Default ratchet floor, in percent. A baseline above it is allowed to fall, so
# long as coverage stays at or above the floor; a baseline at or below it is
# ratcheted. Zero disables the floor.
DEFAULT_RATCHET_FLOOR = 85.0

BASELINE_FILE_OPT = typer.Option(
    Path(".coverage-baseline"), envvar="INPUT_BASELINE_FILE"
)
CURRENT_OPT = typer.Option(..., envvar="CURRENT_PERCENT")
FLOOR_OPT = typer.Option(DEFAULT_RATCHET_FLOOR, envvar="INPUT_RATCHET_FLOOR")
LANGUAGE_OPT = typer.Option("", envvar="INPUT_LANGUAGE")


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
    """Return whether the floor, rather than the dead-band, judges the run."""
    return floor > 0 and baseline > floor


def passes(current: float, baseline: float, floor: float) -> bool:
    """Return whether ``current`` clears the gate for ``baseline``."""
    if floor_mode(baseline, floor):
        return current >= floor
    return current >= baseline - RATCHET_TOLERANCE_PP


def next_baseline(current: float, baseline: float, floor: float) -> float | None:
    """Return the baseline to store after a passing run, or None to hold it."""
    if floor_mode(baseline, floor):
        return current
    if current > baseline + RATCHET_TOLERANCE_PP:
        return current
    return None


def main(
    baseline_file: Path = BASELINE_FILE_OPT,
    *,
    current: float = CURRENT_OPT,
    floor: float = FLOOR_OPT,
    language: str = LANGUAGE_OPT,
) -> None:
    """Gate ``current`` coverage against the baseline and the floor.

    A baseline at or below ``floor`` (or any baseline when ``floor`` is 0) is
    ratcheted: ``current`` more than ``RATCHET_TOLERANCE_PP`` below it fails,
    and the baseline advances only when ``current`` is more than the tolerance
    above it. A baseline above ``floor`` fails only when ``current`` is below
    ``floor``; otherwise the baseline is set to ``current``, with a warning when
    that is a drop of more than the tolerance.
    """
    floor = validate_floor(floor)
    baseline = round(read_baseline(baseline_file), 2)
    current = round(current, 2)

    typer.echo(f"Current coverage: {current}%")
    typer.echo(f"Baseline coverage: {baseline}%")
    typer.echo(f"Tolerance: +/-{RATCHET_TOLERANCE_PP:.2f} percentage points")
    typer.echo(f"Ratchet floor: {floor:g}%")

    if not passes(current, baseline, floor):
        typer.echo("Coverage decreased", err=True)
        raise typer.Exit(code=1)

    if floor_mode(baseline, floor) and current < baseline - RATCHET_TOLERANCE_PP:
        label = f" ({language})" if language else ""
        typer.echo(
            f"::warning title=Coverage dropped{label}::coverage fell from "
            f"{baseline}% to {current}%, still at or above the {floor:g}% floor"
        )

    new = next_baseline(current, baseline, floor)
    if new is not None:
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(f"{new:.2f}")


if __name__ == "__main__":
    typer.run(main)
