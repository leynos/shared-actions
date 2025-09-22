#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.17,<0.18"]
# ///
"""Validate that the caller supplied the expected confirmation string."""

from __future__ import annotations

import typer

EXPECTED_OPTION = typer.Option(..., envvar="EXPECTED")
CONFIRM_OPTION = typer.Option("", envvar="INPUT_CONFIRM")


def main(expected: str = EXPECTED_OPTION, confirm: str = CONFIRM_OPTION) -> None:
    """Validate that the provided confirmation string matches ``expected``.

    Parameters
    ----------
    expected : str
        Confirmation phrase that must be entered to proceed.
    confirm : str
        User-supplied confirmation string collected from workflow input.

    Raises
    ------
    typer.Exit
        Raised when the supplied confirmation does not match ``expected``.
    """
    if confirm != expected:
        typer.echo(
            f"::error::Confirmation failed. Set the 'confirm' input to: {expected}",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("Manual confirmation OK.")


if __name__ == "__main__":
    typer.run(main)
