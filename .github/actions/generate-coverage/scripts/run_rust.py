#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Rust coverage using ``cargo llvm-cov``."""

from __future__ import annotations

import contextlib
import os
import re
import selectors
import shlex
import subprocess
import sys
import threading
import traceback
import typing as t
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path  # noqa: TC003 - used at runtime

import typer
from cmd_utils_loader import run_cmd
from coverage_parsers import get_line_coverage_percent_from_lcov
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError
from shared_utils import read_previous_coverage

try:  # runtime import for graceful fallback
    from lxml import etree
except ImportError as exc:  # pragma: no cover - fail fast if dependency missing
    typer.echo(
        "lxml is required for Cobertura parsing. Install with 'pip install lxml'.",
        err=True,
    )
    raise typer.Exit(1) from exc

if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
FEATURES_OPT = typer.Option("", envvar="INPUT_FEATURES")
WITH_DEFAULT_OPT = typer.Option(default=True, envvar="INPUT_WITH_DEFAULT_FEATURES")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")
CUCUMBER_RS_FEATURES_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_FEATURES")
CUCUMBER_RS_ARGS_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_ARGS")
WITH_CUCUMBER_RS_OPT = typer.Option(default=False, envvar="INPUT_WITH_CUCUMBER_RS")
BASELINE_OPT = typer.Option(None, envvar="BASELINE_RUST_FILE")


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


def _run_cargo(args: list[str]) -> str:
    """Run ``cargo`` with ``args`` streaming output and return ``stdout``."""
    typer.echo(f"$ cargo {shlex.join(args)}")
    proc = cargo[args].popen(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("cargo output streams not captured")  # noqa: TRY003
    stdout_lines: list[str] = []

    if os.name == "nt":
        thread_exceptions: list[Exception] = []

        def pump(src: t.TextIO, *, to_stdout: bool) -> None:
            try:
                for line in iter(src.readline, ""):
                    if to_stdout:
                        typer.echo(line, nl=False)
                        stdout_lines.append(line.rstrip("\r\n"))
                    else:
                        typer.echo(line, err=True, nl=False)
            except Exception as exc:  # noqa: BLE001
                thread_exceptions.append(exc)
                typer.echo(f"Exception in pump thread: {exc}", err=True)
                if os.environ.get("RUN_RUST_DEBUG") == "1":
                    typer.echo(traceback.format_exc(), err=True)

        threads = [
            threading.Thread(
                name="cargo-stdout",
                target=pump,
                args=(proc.stdout,),
                kwargs={"to_stdout": True},
                daemon=True,
            ),
            threading.Thread(
                name="cargo-stderr",
                target=pump,
                args=(proc.stderr,),
                kwargs={"to_stdout": False},
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        # Kill cargo promptly if a pump fails to avoid deadlocks on the other pipe.
        while True:
            if thread_exceptions:
                with contextlib.suppress(Exception):
                    proc.kill()
                break
            if not any(t.is_alive() for t in threads):
                break
            for t in threads:
                t.join(timeout=0.1)
        # Ensure all threads have finished before closing streams.
        for thread in threads:
            thread.join()
        # Streams are guaranteed non-None by earlier guard.
        proc.stdout.close()
        proc.stderr.close()
        if thread_exceptions:
            proc.wait()
            raise thread_exceptions[0]
    else:
        sel = selectors.DefaultSelector()
        try:
            sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
            sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

            while sel.get_map():
                for key, _ in sel.select():
                    line = key.fileobj.readline()
                    if not line:
                        sel.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        typer.echo(line, nl=False)
                        stdout_lines.append(line.rstrip("\r\n"))
                    else:
                        typer.echo(line, err=True, nl=False)
        except Exception:
            # Ensure cargo does not outlive the parent if the selector loop fails.
            with contextlib.suppress(Exception):
                proc.kill()
            proc.wait()
            raise
        finally:
            sel.close()
            # Safe due to earlier guard.
            proc.stdout.close()
            proc.stderr.close()

    retcode = proc.wait()
    if retcode != 0:
        typer.echo(
            f"cargo {shlex.join(args)} failed with code {retcode}",
            err=True,
        )
        raise typer.Exit(code=retcode or 1)
    return "\n".join(stdout_lines)


def _merge_lcov(base: Path, extra: Path) -> None:
    """Merge two lcov files ensuring they end with ``end_of_record``."""
    try:
        base_text = base.read_text()
        extra_text = extra.read_text()
    except (FileNotFoundError, PermissionError) as exc:
        typer.echo(f"Could not read coverage file: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not base_text.rstrip().endswith("end_of_record"):
        typer.echo(f"Malformed lcov data in {base}", err=True)
        raise typer.Exit(1)
    if not extra_text.rstrip().endswith("end_of_record"):
        typer.echo(f"Malformed lcov data in {extra}", err=True)
        raise typer.Exit(1)

    if not base_text.endswith("\n"):
        base_text += "\n"
    if not extra_text.endswith("\n"):
        extra_text += "\n"

    base.write_text(base_text + extra_text)


def run_cucumber_rs_coverage(
    out: Path,
    fmt: str,
    features: str,
    *,
    with_default: bool,
    cucumber_rs_features: str,
    cucumber_rs_args: str,
) -> None:
    """Run cucumber.rs coverage and merge results into ``out``."""
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
        c_args += shlex.split(cucumber_rs_args)

    _run_cargo(c_args)

    if fmt == "cobertura":
        from plumbum.cmd import uvx

        try:
            cmd = uvx["merge-cobertura", str(out), str(cucumber_file)]
            merged = run_cmd(cmd)
        except ProcessExecutionError as exc:
            typer.echo(
                f"merge-cobertura failed with code {exc.retcode}: {exc.stderr}",
                err=True,
            )
            raise typer.Exit(code=exc.retcode or 1) from exc
        out.write_text(merged)
    else:
        _merge_lcov(out, cucumber_file)

    cucumber_file.unlink()


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
    baseline_file: Path | None = BASELINE_OPT,
) -> None:
    """Run cargo llvm-cov and write the output file path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.rust{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    args = get_cargo_coverage_cmd(fmt, out, features, with_default=with_default)
    stdout = _run_cargo(args)

    if with_cucumber_rs and cucumber_rs_features:
        run_cucumber_rs_coverage(
            out,
            fmt,
            features,
            with_default=with_default,
            cucumber_rs_features=cucumber_rs_features,
            cucumber_rs_args=cucumber_rs_args,
        )
    if fmt == "lcov":
        percent = get_line_coverage_percent_from_lcov(out)
    elif fmt == "cobertura":
        percent = get_line_coverage_percent_from_cobertura(out)
    else:
        percent = extract_percent(stdout)

    typer.echo(f"Current coverage: {percent}%")
    previous = read_previous_coverage(baseline_file)
    if previous is not None:
        typer.echo(f"Previous coverage: {previous}%")

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
