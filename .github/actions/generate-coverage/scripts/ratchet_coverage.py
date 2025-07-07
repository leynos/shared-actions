#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
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


def main(
    baseline_file: Path = typer.Option(
        Path(".coverage-baseline"), envvar="INPUT_BASELINE_FILE"
    ),
    current: float = typer.Option(..., envvar="CURRENT_PERCENT"),
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
