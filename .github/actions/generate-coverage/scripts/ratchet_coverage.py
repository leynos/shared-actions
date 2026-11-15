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
from decimal import Decimal, InvalidOperation
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
DEFAULT_RATCHET_FLOOR = "85"

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
            value = float(file.read_text().strip())
        except ValueError:
            return 0.0
        return value if math.isfinite(value) else 0.0
    return 0.0


def validate_floor(text: str) -> Decimal:
    """Parse a floor from its text: 0 to 100, at most two decimal places.

    The text is read as a decimal, never through a float, so a value that a
    float would round (``85.000000000000001``) is refused rather than altered.
    """
    message = (
        "ratchet floor must be a number from 0 to 100 with at most two "
        f"decimal places, got {text!r}"
    )
    try:
        floor = Decimal(text.strip())
    except InvalidOperation:
        raise typer.BadParameter(message) from None
    in_range = floor.is_finite() and 0 <= floor <= 100
    if not in_range or floor != floor.quantize(Decimal("0.01")):
        raise typer.BadParameter(message)
    return floor


def validate_current(current: float) -> float:
    """Return ``current`` if it is a finite number, else refuse it."""
    if not math.isfinite(current):
        message = f"current coverage must be a finite number, got {current}"
        raise typer.BadParameter(message)
    return current


def exact(percent: float) -> Decimal:
    """Return ``percent`` as the decimal it prints as, so comparisons are exact."""
    return Decimal(repr(percent))


def floor_mode(baseline: float, floor: Decimal) -> bool:
    """Return whether the floor, rather than the dead-band, judges the run."""
    return floor > 0 and exact(baseline) > floor


def drop_exceeds_tolerance(current: float, baseline: float) -> bool:
    """Return whether ``current`` is more than the tolerance below ``baseline``."""
    return exact(current) < exact(baseline) - exact(RATCHET_TOLERANCE_PP)


def rise_exceeds_tolerance(current: float, baseline: float) -> bool:
    """Return whether ``current`` is more than the tolerance above ``baseline``."""
    return exact(current) > exact(baseline) + exact(RATCHET_TOLERANCE_PP)


def passes(current: float, baseline: float, floor: Decimal) -> bool:
    """Return whether ``current`` clears the gate for ``baseline``."""
    if floor_mode(baseline, floor):
        return exact(current) >= floor
    return not drop_exceeds_tolerance(current, baseline)


def next_baseline(current: float, baseline: float, floor: Decimal) -> float | None:
    """Return the baseline to store after a passing run, or None to hold it."""
    if floor_mode(baseline, floor) or rise_exceeds_tolerance(current, baseline):
        return current
    return None


def main(
    baseline_file: Path = BASELINE_FILE_OPT,
    *,
    current: float = CURRENT_OPT,
    floor: str = FLOOR_OPT,
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
    floor_value = validate_floor(floor)
    baseline = round(read_baseline(baseline_file), 2)
    current = round(validate_current(current), 2)

    typer.echo(f"Current coverage: {current}%")
    typer.echo(f"Baseline coverage: {baseline}%")
    typer.echo(f"Tolerance: +/-{RATCHET_TOLERANCE_PP:.2f} percentage points")
    typer.echo(f"Ratchet floor: {floor_value:g}%")

    if not passes(current, baseline, floor_value):
        typer.echo("Coverage decreased", err=True)
        raise typer.Exit(code=1)

    if floor_mode(baseline, floor_value) and drop_exceeds_tolerance(current, baseline):
        label = f" ({language})" if language else ""
        typer.echo(
            f"::warning title=Coverage dropped{label}::coverage fell from "
            f"{baseline}% to {current}%, still at or above the {floor_value:g}% floor"
        )

    new = next_baseline(current, baseline, floor_value)
    if new is not None:
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(f"{new:.2f}")


if __name__ == "__main__":
    typer.run(main)
