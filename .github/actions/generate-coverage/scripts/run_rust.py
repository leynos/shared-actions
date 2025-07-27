#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Rust coverage using ``cargo llvm-cov``."""

from __future__ import annotations

import re
from pathlib import Path  # noqa: TC003 - used at runtime

import typer
from coverage_parsers import (
    get_line_coverage_percent_from_cobertura,
    get_line_coverage_percent_from_lcov,
)
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
FEATURES_OPT = typer.Option("", envvar="INPUT_FEATURES")
WITH_DEFAULT_OPT = typer.Option(default=True, envvar="INPUT_WITH_DEFAULT_FEATURES")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def get_cargo_coverage_cmd(
    fmt: str, out: Path, features: str, *, with_default: bool
) -> list[str]:
    """Return the cargo llvm-cov command arguments."""
    args = ["llvm-cov", "--workspace", "--summary-only"]
    if not with_default:
        args.append("--no-default-features")
    if features:
        args += ["--features", features]
    args += [f"--{fmt}", "--output-path", str(out)]
    return args


def extract_percent(output: str) -> str:
    """Return the coverage percentage extracted from ``output``."""
    match = re.search(
        r"(?:coverage|Coverage).*?([0-9]+(?:\.[0-9]+)?)%",
        output,
        re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        typer.echo("Could not parse coverage percent", err=True)
        raise typer.Exit(1)
    return match[1]




def main(
    output_path: Path = OUTPUT_PATH_OPT,
    features: str = FEATURES_OPT,
    *,
    with_default: bool = WITH_DEFAULT_OPT,
    lang: str = LANG_OPT,
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
) -> None:
    """Run cargo llvm-cov and write the output file path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.rust{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    args = get_cargo_coverage_cmd(fmt, out, features, with_default=with_default)

    try:
        retcode, stdout, stderr = cargo[args].run(retcode=None)
    except ProcessExecutionError as exc:  # Guard unexpected failure path
        retcode, stdout, stderr = exc.retcode, exc.stdout, exc.stderr
    if retcode != 0:
        typer.echo(f"cargo llvm-cov failed with code {retcode}: {stderr}", err=True)
        raise typer.Exit(code=retcode or 1)
    typer.echo(stdout)
    if fmt == "lcov":
        percent = get_line_coverage_percent_from_lcov(out)
    elif fmt == "cobertura":
        percent = get_line_coverage_percent_from_cobertura(out)
    else:
        percent = extract_percent(stdout)

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
