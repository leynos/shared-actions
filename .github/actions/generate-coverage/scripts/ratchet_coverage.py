#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Compare current coverage with a baseline within a symmetric dead-band.

Coverage within +/-``RATCHET_TOLERANCE_PP`` percentage points of the stored
baseline is treated as noise: the run passes and the baseline is held. A drop
beyond the band fails the gate; a rise beyond the band advances the baseline.
"""

from __future__ import annotations

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

BASELINE_FILE_OPT = typer.Option(
    Path(".coverage-baseline"), envvar="INPUT_BASELINE_FILE"
)
CURRENT_OPT = typer.Option(..., envvar="CURRENT_PERCENT")


def read_baseline(file: Path) -> float:
    """Return the stored baseline coverage or 0.0 if missing/invalid."""
    if file.is_file():
        try:
            return float(file.read_text().strip())
        except ValueError:
            return 0.0
    return 0.0


def main(
    baseline_file: Path = BASELINE_FILE_OPT,
    *,
    current: float = CURRENT_OPT,
) -> None:
    """Gate ``current`` coverage against the baseline within the dead-band.

    Fails when ``current`` is more than ``RATCHET_TOLERANCE_PP`` below the
    baseline, advances the baseline when ``current`` is more than
    ``RATCHET_TOLERANCE_PP`` above it, and otherwise passes while leaving the
    baseline unchanged.
    """
    baseline = round(read_baseline(baseline_file), 2)
    current = round(current, 2)

    typer.echo(f"Current coverage: {current}%")
    typer.echo(f"Baseline coverage: {baseline}%")
    typer.echo(f"Tolerance: +/-{RATCHET_TOLERANCE_PP:.2f} percentage points")

    # Fail only when coverage falls more than the tolerance band below the
    # baseline.
    if current < baseline - RATCHET_TOLERANCE_PP:
        typer.echo("Coverage decreased", err=True)
        raise typer.Exit(code=1)

    # Advance the baseline only on a genuine improvement beyond the band. Within
    # +/- the band (either side of the baseline) the run passes and the baseline
    # is held: a low run is not failed and a lucky-high run does not inflate the
    # baseline (which would then make the next normal run fail).
    if current > baseline + RATCHET_TOLERANCE_PP:
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(f"{current:.2f}")


if __name__ == "__main__":
    typer.run(main)
