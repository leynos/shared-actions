#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Rust coverage using ``cargo llvm-cov``."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path  # noqa: TC003 - used at runtime

import typer
from coverage_parsers import get_line_coverage_percent_from_lcov

try:  # runtime import for graceful fallback
    from lxml import etree
except ImportError as exc:  # pragma: no cover - fail fast if dependency missing
    typer.echo(
        "lxml is required for Cobertura parsing. Install with 'pip install lxml'.",
        err=True,
    )
    raise typer.Exit(1) from exc
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
FEATURES_OPT = typer.Option("", envvar="INPUT_FEATURES")
WITH_DEFAULT_OPT = typer.Option(default=True, envvar="INPUT_WITH_DEFAULT_FEATURES")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")
CUCUMBER_RS_FEATURES_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_FEATURES")
CUCUMBER_RS_ARGS_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_ARGS")
WITH_CUCUMBER_RS_OPT = typer.Option(default=False, envvar="INPUT_WITH_CUCUMBER_RS")


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


def _format_percent(covered: int, total: int) -> str:
    pct = Decimal(covered) * Decimal(100) / Decimal(total)
    return str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_line_coverage_percent_from_cobertura(xml_file: Path) -> str:
    """Return overall line coverage % from a Cobertura XML file."""
    try:
        root = etree.parse(str(xml_file)).getroot()
    except (FileNotFoundError, PermissionError) as exc:
        typer.echo(f"Could not read {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc
    except etree.XMLSyntaxError as exc:
        typer.echo(f"Invalid XML in {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        total = int(root.xpath("count(//class/lines/line)"))
        covered = int(root.xpath("count(//class/lines/line[@hits>0])"))
    except etree.XPathError as exc:
        typer.echo(f"Malformed Cobertura data: {exc}", err=True)
        raise typer.Exit(1) from exc

    if total == 0:
        try:
            covered = int(root.xpath("number(/coverage/@lines-covered)"))
            total = int(root.xpath("number(/coverage/@lines-valid)"))
        except etree.XPathError as exc:
            typer.echo(f"Cobertura summary missing: {exc}", err=True)
            raise typer.Exit(1) from exc

    if total == 0:
        return "0.00"

    return _format_percent(covered, total)




def main(
    output_path: Path = OUTPUT_PATH_OPT,
    features: str = FEATURES_OPT,
    *,
    with_default: bool = WITH_DEFAULT_OPT,
    lang: str = LANG_OPT,
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
    cucumber_rs_features: str = CUCUMBER_RS_FEATURES_OPT,
    cucumber_rs_args: str = CUCUMBER_RS_ARGS_OPT,
    with_cucumber_rs: bool = WITH_CUCUMBER_RS_OPT,
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

    cucumber_file: Path | None = None
    if with_cucumber_rs and cucumber_rs_features:
        cucumber_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
        c_args = get_cargo_coverage_cmd(
            fmt,
            cucumber_file,
            features,
            with_default=with_default,
        )
        c_args += [
            "--",
            "--test",
            "cucumber",
            "--",
            "cucumber",
            "--features",
            cucumber_rs_features,
        ]
        if cucumber_rs_args:
            c_args += cucumber_rs_args.split()
        try:
            retcode, c_out, c_err = cargo[c_args].run(retcode=None)
        except ProcessExecutionError as exc:
            retcode, c_out, c_err = exc.retcode, exc.stdout, exc.stderr
        if retcode != 0:
            typer.echo(
                f"cargo llvm-cov failed with code {retcode}: {c_err}",
                err=True,
            )
            raise typer.Exit(code=retcode or 1)
        typer.echo(c_out)
        if fmt == "cobertura":
            from plumbum.cmd import uvx
            try:
                merged = uvx["merge-cobertura", str(out), str(cucumber_file)]()
            except ProcessExecutionError as exc:
                typer.echo(
                    f"merge-cobertura failed with code {exc.retcode}: {exc.stderr}",
                    err=True,
                )
                raise typer.Exit(code=exc.retcode or 1) from exc
            out.write_text(merged)
            cucumber_file.unlink()
        else:
            out.write_text(out.read_text() + cucumber_file.read_text())
            cucumber_file.unlink()
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
