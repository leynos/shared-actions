#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Compare current coverage with baseline and update if needed."""

from __future__ import annotations

from pathlib import Path

import typer


def read_baseline(file: Path) -> float:
    """Return the stored baseline coverage or 0.0 if missing/invalid."""
    if file.is_file():
        try:
            return float(file.read_text().strip())
        except ValueError:
            return 0.0
    return 0.0


BASELINE_FILE_OPT = typer.Option(
    Path(".coverage-baseline"), envvar="INPUT_BASELINE_FILE"
)
CURRENT_OPT = typer.Option(..., envvar="CURRENT_PERCENT")


def main(
    baseline_file: Path = BASELINE_FILE_OPT,
    *,
    current: float = CURRENT_OPT,
) -> None:
    """Compare ``current`` coverage with the stored baseline and update it."""
    baseline = round(read_baseline(baseline_file), 2)
    current = round(current, 2)

    typer.echo(f"Current coverage: {current}%")
    typer.echo(f"Baseline coverage: {baseline}%")

    if current < baseline:
        typer.echo("Coverage decreased", err=True)
        raise typer.Exit(code=1)

    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(f"{current:.2f}")


if __name__ == "__main__":
    typer.run(main)
