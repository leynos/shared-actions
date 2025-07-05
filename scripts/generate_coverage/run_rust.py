#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
from pathlib import Path

import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError


def get_cargo_coverage_cmd(
    fmt: str, out: Path, features: str, with_default: bool
) -> list[str]:
    """Return the cargo llvm-cov command arguments."""
    args = ["llvm-cov", "--workspace"]
    if not with_default:
        args.append("--no-default-features")
    if features:
        args += ["--features", features]
    args += [f"--{fmt}", "--output-path", str(out)]
    return args


def main(
    output_path: Path = typer.Option(..., envvar="INPUT_OUTPUT_PATH"),
    features: str = typer.Option("", envvar="INPUT_FEATURES"),
    with_default: bool = typer.Option(True, envvar="INPUT_WITH_DEFAULT_FEATURES"),
    lang: str = typer.Option(..., envvar="DETECTED_LANG"),
    fmt: str = typer.Option(..., envvar="DETECTED_FMT"),
    github_output: Path = typer.Option(..., envvar="GITHUB_OUTPUT"),
) -> None:
    """Run cargo llvm-cov and write the output file path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.rust{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    args = get_cargo_coverage_cmd(fmt, out, features, with_default)

    try:
        cargo[args]()
    except ProcessExecutionError as exc:
        typer.echo(
            f"cargo llvm-cov failed with code {exc.retcode}: {exc.stderr}", err=True
        )
        raise typer.Exit(code=exc.retcode or 1) from exc

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")


if __name__ == "__main__":
    typer.run(main)
