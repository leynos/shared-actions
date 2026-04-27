#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Python coverage analysis using slipcover and pytest."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003 - used at runtime
import contextlib
import shutil
import typing as typ
from pathlib import Path

import typer
from cmd_utils_loader import run_cmd
from coverage_parsers import get_line_coverage_percent_from_cobertura
from plumbum import local
from plumbum.cmd import uv
from plumbum.commands.processes import ProcessExecutionError
from shared_utils import read_previous_coverage

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from plumbum.commands.base import BoundCommand

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")
BASELINE_OPT = typer.Option(None, envvar="BASELINE_PYTHON_FILE")
COVERAGE_VENV = Path(".venv-coverage")
TOOLING_PACKAGES: tuple[str, ...] = ("slipcover", "pytest", "coverage")
_COVERAGE_PYTHON_CMD: BoundCommand | None = None

SLIPCOVER_ARGS: tuple[str, ...] = (
    "-m",
    "slipcover",
    "--branch",
)
PYTEST_ARGS: tuple[str, ...] = (
    "-m",
    "pytest",
    "-v",
)


def _coverage_python_path() -> Path:
    """Return the Python executable path inside the coverage venv."""
    candidates = (
        COVERAGE_VENV / "bin" / "python",
        COVERAGE_VENV / "Scripts" / "python.exe",
        COVERAGE_VENV / "Scripts" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    paths = ", ".join(str(candidate) for candidate in candidates)
    msg = f"Coverage venv Python executable not found; checked: {paths}"
    raise RuntimeError(msg)


def create_venv() -> str:
    """Create a throwaway venv for coverage tooling; return python path."""
    if not COVERAGE_VENV.exists():
        run_cmd(uv["venv", str(COVERAGE_VENV)])
    try:
        return str(_coverage_python_path())
    except RuntimeError:
        if COVERAGE_VENV.is_dir() and not COVERAGE_VENV.is_symlink():
            shutil.rmtree(COVERAGE_VENV)
        else:
            COVERAGE_VENV.unlink(missing_ok=True)
        run_cmd(uv["venv", str(COVERAGE_VENV)])
        return str(_coverage_python_path())


def install_coverage_tools(python: str) -> None:
    """Install coverage tooling into the throwaway venv."""
    run_cmd(uv["pip", "install", "--python", python, *TOOLING_PACKAGES])


def _coverage_python_cmd() -> BoundCommand:
    """Return a python command with coverage tooling available."""
    global _COVERAGE_PYTHON_CMD
    if _COVERAGE_PYTHON_CMD is None:
        python = create_venv()
        install_coverage_tools(python)
        _COVERAGE_PYTHON_CMD = local[python]
    return _COVERAGE_PYTHON_CMD


def _coverage_args(fmt: str, out: Path) -> list[str]:
    """Return the slipcover/pytest argv for the requested format."""
    args: list[str] = [*SLIPCOVER_ARGS]
    if fmt == "cobertura":
        # slipcover treats --xml as a boolean flag; --out sets the report path
        args.extend(["--xml", "--out", str(out)])
    args.extend(PYTEST_ARGS)
    return args


def coverage_cmd_for_fmt(fmt: str, out: Path) -> BoundCommand:
    """Return the slipcover command for the requested format."""
    python_cmd = _coverage_python_cmd()
    return python_cmd[_coverage_args(fmt, out)]


@contextlib.contextmanager
def tmp_coveragepy_xml(out: Path) -> cabc.Generator[Path]:
    """Generate a cobertura XML from coverage.py and clean up afterwards."""
    xml_tmp = out.with_suffix(".xml")
    python_cmd = _coverage_python_cmd()
    try:
        cmd = python_cmd["-m", "coverage", "xml", "-o", str(xml_tmp)]
        run_cmd(cmd)
    except ProcessExecutionError as exc:
        typer.echo(
            f"coverage xml failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise typer.Exit(code=exc.retcode or 1) from exc
    try:
        yield xml_tmp
    finally:
        xml_tmp.unlink(missing_ok=True)


def main(
    output_path: Path = OUTPUT_PATH_OPT,
    lang: str = LANG_OPT,
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
    baseline_file: Path | None = BASELINE_OPT,
) -> None:
    """Run slipcover coverage and write the output path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.python{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = coverage_cmd_for_fmt(fmt, out)
    try:
        run_cmd(cmd, method="run_fg")
    except ProcessExecutionError as exc:
        raise typer.Exit(code=exc.retcode or 1) from exc

    if fmt == "coveragepy":
        with tmp_coveragepy_xml(out) as xml_tmp:
            percent = get_line_coverage_percent_from_cobertura(xml_tmp)
        Path(".coverage").replace(out)
    else:
        percent = get_line_coverage_percent_from_cobertura(out)

    typer.echo(f"Current coverage: {percent}%")
    previous = read_previous_coverage(baseline_file)
    if previous is not None:
        typer.echo(f"Previous coverage: {previous}%")

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
