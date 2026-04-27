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
# _COVERAGE_PYTHON_CMD is a module-level lazy singleton.  GitHub Actions
# runners execute action steps sequentially in a single thread, so no
# synchronisation is required.  The variable is None until the first call
# to _coverage_python_cmd(), after which it is reused for the lifetime of
# the process.
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
    """Create a throwaway venv for coverage tooling.

    If the venv directory already exists but its Python executable cannot be
    located (broken-cache state), the directory is removed and the venv is
    recreated before returning the interpreter path.

    Returns
    -------
    str
        Absolute path to the Python executable inside the created venv.
    """
    if not COVERAGE_VENV.exists():
        typer.echo(f"Creating coverage venv at {COVERAGE_VENV}")
        run_cmd(uv["venv", str(COVERAGE_VENV)])
    else:
        typer.echo(f"Reusing existing coverage venv at {COVERAGE_VENV}")
    try:
        python = str(_coverage_python_path())
    except RuntimeError:
        typer.echo(
            f"Coverage venv at {COVERAGE_VENV} is missing its Python "
            "executable; recreating.",
            err=True,
        )
        shutil.rmtree(COVERAGE_VENV)
        run_cmd(uv["venv", str(COVERAGE_VENV)])
        python = str(_coverage_python_path())
    return python


def install_coverage_tools(python: str) -> None:
    """Install coverage tooling into the throwaway venv.

    Parameters
    ----------
    python : str
        Path to the Python executable inside the target venv, as returned by
        create_venv().

    Raises
    ------
    plumbum.commands.processes.ProcessExecutionError
        Propagated from run_cmd() when uv pip install fails.
    """
    typer.echo(f"Installing coverage tooling {TOOLING_PACKAGES} into {COVERAGE_VENV}")
    run_cmd(uv["pip", "install", "--python", python, *TOOLING_PACKAGES])


def _coverage_python_cmd() -> BoundCommand:
    """Set up the coverage venv on first call and return the cached command.

    Side effects on first call
    --------------------------
    * Creates .venv-coverage via create_venv() (recreates on broken cache).
    * Installs slipcover, pytest, and coverage into the venv.
    * Caches the resulting BoundCommand in _COVERAGE_PYTHON_CMD.

    Returns
    -------
    BoundCommand
        A plumbum command bound to the venv's Python executable.
    """
    global _COVERAGE_PYTHON_CMD
    if _COVERAGE_PYTHON_CMD is not None:
        typer.echo("Reusing cached coverage Python command.")
        return _COVERAGE_PYTHON_CMD
    typer.echo("Setting up coverage Python environment (first use).")
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
