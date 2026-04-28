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
from functools import lru_cache
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
PROJECT_SYNC_ARGS: tuple[str, ...] = ("sync", "--inexact", "--python")

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


def _find_coverage_python() -> Path | None:
    """Return the coverage venv Python executable path when it exists."""
    if COVERAGE_VENV.is_symlink() or not COVERAGE_VENV.is_dir():
        return None
    candidates = (
        COVERAGE_VENV / "bin" / "python",
        COVERAGE_VENV / "Scripts" / "python.exe",
        COVERAGE_VENV / "Scripts" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _remove_coverage_venv() -> None:
    """Remove the coverage venv directory or non-directory placeholder.

    Uses ``shutil.rmtree`` for directories and ``Path.unlink`` for any
    other filesystem object (e.g. a symlink or a stale file).
    """
    if COVERAGE_VENV.is_dir() and not COVERAGE_VENV.is_symlink():
        shutil.rmtree(COVERAGE_VENV)
    else:
        COVERAGE_VENV.unlink(missing_ok=True)


def _recreate_coverage_venv() -> Path:
    """Remove any existing broken venv, create a fresh one, and return its Python.

    Returns
    -------
    Path
        Absolute path to the Python executable inside the newly created venv.

    Raises
    ------
    RuntimeError
        If the Python executable cannot be located after creation.
    """
    if COVERAGE_VENV.exists():
        typer.echo(
            f"Coverage venv at {COVERAGE_VENV} is missing its Python "
            "executable; recreating.",
            err=True,
        )
        _remove_coverage_venv()
    else:
        typer.echo(f"Creating coverage venv at {COVERAGE_VENV}")
    run_cmd(uv["venv", str(COVERAGE_VENV)])
    python = _find_coverage_python()
    if python is None:
        msg = f"Coverage venv Python executable not found in {COVERAGE_VENV}"
        raise RuntimeError(msg)
    return python


def _ensure_coverage_venv() -> str:
    """Create or repair the coverage venv and install project/test tooling.

    Returns the Python executable path inside the isolated coverage venv.
    """
    python = _find_coverage_python()
    if python is None:
        python = _recreate_coverage_venv()
    typer.echo(f"Installing project dependencies into {COVERAGE_VENV}")
    try:
        run_cmd(uv[*PROJECT_SYNC_ARGS, str(python)])
    except ProcessExecutionError as exc:
        typer.echo(
            f"uv sync failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise
    typer.echo(f"Installing coverage tooling {TOOLING_PACKAGES} into {COVERAGE_VENV}")
    try:
        run_cmd(uv["pip", "install", "--python", str(python), *TOOLING_PACKAGES])
    except ProcessExecutionError as exc:
        typer.echo(
            f"uv pip install failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise
    return str(python)


@lru_cache(maxsize=1)
def _coverage_python_cmd() -> BoundCommand:
    """Return the coverage venv Python command, creating it on first use."""
    python = _ensure_coverage_venv()
    return local[python]


def _coverage_args(fmt: str, out: Path) -> list[str]:
    """Return the slipcover/pytest argv for the requested format."""
    args: list[str] = [*SLIPCOVER_ARGS]
    if fmt == "cobertura":
        # slipcover treats --xml as a boolean flag; --out sets the report path
        args.extend(["--xml", "--out", str(out)])
    args.extend(PYTEST_ARGS)
    return args


def coverage_cmd_for_fmt(fmt: str, out: Path) -> BoundCommand:
    """Return the slipcover command for the requested coverage format.

    Parameters
    ----------
    fmt : str
        Coverage format identifier. ``"cobertura"`` adds slipcover's
        ``--xml`` and ``--out`` flags; all other values produce a bare
        slipcover/pytest invocation.
    out : Path
        Destination path for the coverage output file; passed to slipcover's
        ``--out`` argument when ``fmt == "cobertura"``.

    Returns
    -------
    plumbum.commands.base.BoundCommand
        A plumbum command that runs slipcover via the coverage venv Python.
    """
    python_cmd = _coverage_python_cmd()
    return python_cmd[_coverage_args(fmt, out)]


@contextlib.contextmanager
def tmp_coveragepy_xml(out: Path) -> cabc.Generator[Path]:
    """Generate a Cobertura XML from coverage.py and clean it up afterwards.

    Invokes ``python -m coverage xml -o <xml_tmp>`` using the coverage venv
    Python, yields the temporary XML path for the caller to consume, and
    removes the file on exit - whether the body raised or returned normally.

    Parameters
    ----------
    out : Path
        Path to the ``.dat`` (coverage.py data) file.  The temporary XML is
        written to ``out.with_suffix(".xml")``.

    Yields
    ------
    Path
        Absolute path to the freshly generated temporary Cobertura XML file.

    Raises
    ------
    typer.Exit
        If ``coverage xml`` exits with a non-zero return code.
    """
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
    """Run slipcover coverage and write the result to ``GITHUB_OUTPUT``.

    Parameters
    ----------
    output_path : Path
        Destination path for the coverage output file.
    lang : str
        Detected project language (``"rust"``, ``"python"``, or
        ``"mixed"``).  When ``"mixed"``, the output file is renamed to
        include a ``.python`` infix.
    fmt : str
        Coverage format identifier passed to :func:`coverage_cmd_for_fmt`.
    github_output : Path
        Path to the ``GITHUB_OUTPUT`` append file where ``file=`` and
        ``percent=`` are written.
    baseline_file : Path or None
        Optional path to a previous coverage baseline file.  When present,
        the previous percentage is echoed to the log.
    """
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
