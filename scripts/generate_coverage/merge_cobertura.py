#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from pathlib import Path

import click
import typer
from plumbum.cmd import uvx
from plumbum.commands.processes import ProcessExecutionError


class ExistingFile(click.ParamType):
    name = "file"

    def __init__(self, kind: str) -> None:
        """Create a validator that ensures the file exists."""
        self.kind = kind

    def convert(
        self, value: str, param: click.Parameter | None, ctx: click.Context | None
    ) -> Path:  # type: ignore[override]
        """Validate that the provided path points to an existing file."""
        path = Path(value)
        if not path.is_file():
            self.fail(f"{self.kind} file not found: {value}", param, ctx)
        return path


def main(
    rust_file: Path = typer.Option(
        ..., envvar="RUST_FILE", type=ExistingFile("Rust coverage")
    ),
    python_file: Path = typer.Option(
        ..., envvar="PYTHON_FILE", type=ExistingFile("Python coverage")
    ),
    output_path: Path = typer.Option(..., envvar="OUTPUT_PATH"),
) -> None:
    """Merge two cobertura XML files and delete the inputs."""
    try:
        output = uvx["merge-cobertura", str(rust_file), str(python_file)]()
    except ProcessExecutionError as exc:
        typer.echo(
            f"merge-cobertura failed with code {exc.retcode}: {exc.stderr}", err=True
        )
        raise typer.Exit(code=exc.retcode or 1) from exc
    output_path.write_text(output)
    rust_file.unlink()
    python_file.unlink()


if __name__ == "__main__":
    typer.run(main)
