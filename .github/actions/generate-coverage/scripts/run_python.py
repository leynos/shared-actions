#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Python coverage analysis using slipcover and pytest."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003 - used at runtime
import contextlib
import logging
import os
import shutil
import typing as typ
from functools import lru_cache
from pathlib import Path

import typer
from cmd_utils_loader import run_cmd
from common import _required_env
from coverage_parsers import get_line_coverage_percent_from_cobertura
from plumbum import local
from plumbum.cmd import uv
from plumbum.commands.processes import ProcessExecutionError
from shared_utils import read_previous_coverage

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from plumbum.commands.base import BoundCommand

logger = logging.getLogger(__name__)

# COVERAGE_VENV is a process-scoped constant.  It is consumed by
# _ensure_coverage_venv() and _coverage_python_cmd(), both of which are
# called from a single-threaded GitHub Actions step.
COVERAGE_VENV = Path(".venv-coverage")
TOOLING_PACKAGES: tuple[str, ...] = (
    # 1.0.18 is the first release with the xdist plugin that lets the
    # default `pytest -n auto` runs merge worker coverage correctly.
    "slipcover>=1.0.18",
    "pytest",
    "pytest-xdist",
    "coverage",
)
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
DEFAULT_PYTEST_WORKERS = "auto"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s %(message)s",
)


def _coverage_python_candidates() -> tuple[Path, ...]:
    """Return the supported Python executable locations inside the venv."""
    return (
        COVERAGE_VENV / "bin" / "python",
        COVERAGE_VENV / "Scripts" / "python.exe",
        COVERAGE_VENV / "Scripts" / "python",
    )


def _find_coverage_python() -> Path | None:
    """Return the coverage venv Python executable path when it exists.

    Virtual environment Python executables are commonly symlinks to the base
    interpreter. Keep the venv path so uv targets the venv instead of the
    externally managed system Python.
    """
    if COVERAGE_VENV.is_symlink() or not COVERAGE_VENV.is_dir():
        return None
    for candidate in _coverage_python_candidates():
        if candidate.is_file():
            return candidate.absolute()
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
    if COVERAGE_VENV.exists() or COVERAGE_VENV.is_symlink():
        typer.echo(
            f"Coverage venv at {COVERAGE_VENV} is missing its Python "
            "executable; recreating.",
            err=True,
        )
        _remove_coverage_venv()
    else:
        typer.echo(f"Creating coverage venv at {COVERAGE_VENV}")
    run_cmd(uv["venv", str(COVERAGE_VENV)])
    typer.echo(f"Coverage venv created at {COVERAGE_VENV}")
    python = _find_coverage_python()
    if python is None:
        msg = f"Coverage venv Python executable not found in {COVERAGE_VENV}"
        raise RuntimeError(msg)
    return python


@contextlib.contextmanager
def _project_env(venv: Path) -> cabc.Iterator[None]:
    """Temporarily set UV_PROJECT_ENVIRONMENT to the given venv path."""
    previous = os.environ.get("UV_PROJECT_ENVIRONMENT")
    os.environ["UV_PROJECT_ENVIRONMENT"] = str(venv.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("UV_PROJECT_ENVIRONMENT", None)
        else:
            os.environ["UV_PROJECT_ENVIRONMENT"] = previous


def _sync_project_deps(python: Path) -> None:
    """Run `uv sync` for the project into the coverage venv, with logs."""
    typer.echo(f"Installing project dependencies into {COVERAGE_VENV}")
    try:
        with _project_env(COVERAGE_VENV):
            run_cmd(uv[*PROJECT_SYNC_ARGS, str(python)])
        typer.echo(f"Project dependencies installed into {COVERAGE_VENV}")
    except ProcessExecutionError as exc:
        typer.echo(
            f"uv sync failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise


def _install_coverage_tooling(python: Path) -> None:
    """Install slipcover/pytest/coverage into the venv, with logs."""
    typer.echo(f"Installing coverage tooling {TOOLING_PACKAGES} into {COVERAGE_VENV}")
    try:
        run_cmd(uv["pip", "install", "--python", str(python), *TOOLING_PACKAGES])
    except ProcessExecutionError as exc:
        typer.echo(
            f"uv pip install failed with code {exc.retcode}: {exc.stderr}",
            err=True,
        )
        raise
    typer.echo(f"Coverage tooling installed into {COVERAGE_VENV}")


def _acquire_coverage_python() -> Path:
    """Discover or create the coverage venv and return its Python path.

    Returns
    -------
    Path
        Absolute path to the Python executable inside the coverage venv.

    Raises
    ------
    RuntimeError
        Propagated from :func:`_recreate_coverage_venv` when the Python
        executable cannot be located after venv creation.
    """
    candidates = _coverage_python_candidates()
    logger.debug(
        "checking coverage venv Python candidates",
        extra={
            "coverage_venv": str(COVERAGE_VENV),
            "candidates": [str(c) for c in candidates],
        },
    )
    python = _find_coverage_python()
    if python is None:
        python = _recreate_coverage_venv()
        logger.debug(
            "created fresh coverage venv",
            extra={
                "coverage_venv": str(COVERAGE_VENV),
                "python": str(python),
            },
        )
    else:
        typer.echo(f"Reusing existing coverage venv at {COVERAGE_VENV}")
        raw_candidate = next(
            (candidate for candidate in candidates if candidate.absolute() == python),
            python,
        )
        logger.debug(
            "selected coverage venv Python candidate",
            extra={
                "coverage_venv": str(COVERAGE_VENV),
                "candidate": str(raw_candidate),
                "candidate_absolute": str(raw_candidate.absolute()),
                "candidate_resolved": str(raw_candidate.resolve(strict=False)),
                "is_symlink": raw_candidate.is_symlink(),
                "python": str(python),
                "resolved_python": str(raw_candidate.resolve(strict=False)),
                "preserved_symlink": raw_candidate.is_symlink(),
            },
        )
    return python


def _ensure_coverage_venv() -> str:
    """Create or repair the coverage venv and install project/test tooling.

    Delegates venv discovery and creation to _acquire_coverage_python, then
    runs ``uv sync`` to install project dependencies, followed by
    ``uv pip install`` to add ``slipcover``, ``pytest``, and ``coverage``.

    Returns
    -------
    str
        Absolute path to the Python executable inside the coverage venv.

    Raises
    ------
    RuntimeError
        Propagated from :func:`_recreate_coverage_venv` when the Python
        executable cannot be located after venv creation.
    plumbum.commands.processes.ProcessExecutionError
        Propagated from ``uv sync`` or ``uv pip install`` when either
        command exits with a non-zero return code.
    """
    python = _acquire_coverage_python()
    logger.info(
        "using coverage venv Python for uv commands",
        extra={
            "coverage_venv": str(COVERAGE_VENV),
            "python": str(python),
            "sync_args": [*PROJECT_SYNC_ARGS, str(python)],
            "tooling_packages": [*TOOLING_PACKAGES],
        },
    )
    _sync_project_deps(python)
    logger.info(
        "installing coverage tooling with uv pip",
        extra={
            "coverage_venv": str(COVERAGE_VENV),
            "python": str(python),
            "pip_args": ["pip", "install", "--python", str(python), *TOOLING_PACKAGES],
        },
    )
    _install_coverage_tooling(python)
    return str(python)


# _coverage_python_cmd() is memoised with lru_cache rather than using a
# mutable global.  GitHub Actions executes action steps sequentially in a
# single thread, so no synchronisation is required; the cache is safe to
# use without a lock for the lifetime of this process.
@lru_cache(maxsize=1)
def _coverage_python_cmd() -> BoundCommand:
    """Return the coverage venv Python command, creating it on first use."""
    python = _ensure_coverage_venv()
    return local[python]


_VALID_NAMED_WORKERS = frozenset({"auto", "logical"})


def _parse_pytest_workers(raw: str | None) -> str:
    """Parse and validate the pytest-workers value; raise ValueError on bad input.

    This function is pure: it performs no I/O and has no side-effects.
    Callers that need CLI error handling should use _normalise_pytest_workers.
    """
    if raw is None:
        return ""
    value = raw.strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in _VALID_NAMED_WORKERS:
        return lowered
    # `str.isdecimal()` matches `int()`'s acceptance set (decimal-numeric
    # Unicode digits, "Nd"). Plain `str.isdigit()` also returns True for
    # forms like the superscript "²" which `int()` rejects, so the int()
    # call below would crash with a cryptic Python message instead of the
    # canonical "Invalid pytest-workers value" error.
    if value.isdecimal() and int(value) > 0:
        return value
    message = (
        f"Invalid pytest-workers value: {raw!r}. Expected a positive integer, "
        '"auto", "logical", or "" to disable parallelism.'
    )
    raise ValueError(message)


def _normalise_pytest_workers(raw: str | None) -> str:
    """Validate and normalise the pytest-workers value, exiting on invalid input.

    Delegates pure validation to _parse_pytest_workers.  Any ValueError
    raised there is converted into a CLI error message on stderr and
    typer.Exit with code 2 — making this function's side-effects explicit
    by design.
    """
    try:
        return _parse_pytest_workers(raw)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _coverage_args(fmt: str, out: Path, workers: str = "") -> list[str]:
    """Return the slipcover/pytest argv for the requested format."""
    args: list[str] = [*SLIPCOVER_ARGS]
    if fmt == "cobertura":
        # slipcover treats --xml as a boolean flag; --out sets the report path
        args.extend(["--xml", "--out", str(out)])
    args.extend(PYTEST_ARGS)
    if workers:
        args.extend(["-n", workers])
    return args


def coverage_cmd_for_fmt(fmt: str, out: Path, workers: str = "") -> BoundCommand:
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
    workers : str
        Worker count for pytest-xdist's ``-n`` flag. Empty disables xdist;
        otherwise must already be a validated value such as ``"auto"`` or a
        non-negative integer string.

    Returns
    -------
    plumbum.commands.base.BoundCommand
        A plumbum command that runs slipcover via the coverage venv Python.
    """
    python_cmd = _coverage_python_cmd()
    return python_cmd[_coverage_args(fmt, out, workers)]


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


def _resolve_output_path(output_path: Path, lang: str) -> Path:
    """Return the effective output path, accounting for mixed-language projects.

    Parameters
    ----------
    output_path : Path
        Base output path supplied by the caller.
    lang : str
        Detected project language.  When ``"mixed"``, a ``.python`` infix
        is inserted between the stem and the suffix.

    Returns
    -------
    Path
        Adjusted output path.
    """
    if lang == "mixed":
        return output_path.with_name(f"{output_path.stem}.python{output_path.suffix}")
    return output_path


def _run_coverage(fmt: str, out: Path, workers: str = "") -> str:
    """Run slipcover and return the line coverage percentage.

    Parameters
    ----------
    fmt : str
        Coverage format identifier passed to :func:`coverage_cmd_for_fmt`.
    out : Path
        Destination path for the coverage output file.
    workers : str
        Worker count for pytest-xdist's ``-n`` flag; empty disables xdist.

    Returns
    -------
    str
        Line coverage percentage parsed from the generated report.

    Raises
    ------
    typer.Exit
        With the subprocess return code when the slipcover/coverage
        command exits non-zero, or when ``coverage xml`` fails in
        ``coveragepy`` format mode.
    """
    try:
        cmd = coverage_cmd_for_fmt(fmt, out, workers)
        run_cmd(cmd, method="run_fg")
    except ProcessExecutionError as exc:
        raise typer.Exit(code=exc.retcode or 1) from exc
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if fmt == "coveragepy":
        with tmp_coveragepy_xml(out) as xml_tmp:
            percent = get_line_coverage_percent_from_cobertura(xml_tmp)
        Path(".coverage").replace(out)
        return percent
    return get_line_coverage_percent_from_cobertura(out)


def _resolve_pytest_workers(pytest_workers: str | None) -> str:
    """Resolve and validate the pytest-workers value; raise ValueError on invalid.

    Sources the raw value from the CLI option, the ``INPUT_PYTEST_WORKERS``
    env var, or ``DEFAULT_PYTEST_WORKERS``.  Validation is delegated to the
    pure :func:`_parse_pytest_workers`.  CLI side-effects (``typer.echo`` /
    ``typer.Exit``) are the caller's responsibility.
    """
    if pytest_workers is None:
        raw: str | None = os.getenv("INPUT_PYTEST_WORKERS")
        if raw is None:
            raw = DEFAULT_PYTEST_WORKERS
    else:
        raw = pytest_workers
    return _parse_pytest_workers(raw)


def _resolve_inputs(
    output_path: Path | None,
    lang: str | None,
    fmt: str | None,
    github_output: Path | None,
) -> tuple[Path, str, Path]:
    """Resolve CLI inputs and return the effective output path."""
    resolved_output_path = output_path or Path(_required_env("INPUT_OUTPUT_PATH"))
    resolved_lang = lang or _required_env("DETECTED_LANG")
    resolved_fmt = fmt or _required_env("DETECTED_FMT")
    resolved_github_output = github_output or Path(_required_env("GITHUB_OUTPUT"))
    out = _resolve_output_path(resolved_output_path, resolved_lang)
    return out, resolved_fmt, resolved_github_output


def _emit_github_output(path: Path, percent: str, github_output: Path) -> None:
    """Write coverage outputs for later GitHub Actions steps."""
    with github_output.open("a") as fh:
        fh.write(f"file={path}\n")
        fh.write(f"percent={percent}\n")


_OutputPathOption = typ.Annotated[
    Path | None,
    typer.Option(
        help="Destination path for the coverage output file.",
    ),
]
_LangOption = typ.Annotated[
    str | None,
    typer.Option(
        help='Detected project language: "rust", "python", or "mixed".',
    ),
]
_FmtOption = typ.Annotated[
    str | None,
    typer.Option(
        help='Coverage format: "slipcover", "coveragepy", etc.',
    ),
]
_GithubOutputOption = typ.Annotated[
    Path | None,
    typer.Option(
        help="Path to the GitHub Actions output file.",
    ),
]
_BaselineFileOption = typ.Annotated[
    Path | None,
    typer.Option(
        envvar="BASELINE_PYTHON_FILE",
        help="Optional path to a previous coverage baseline file.",
    ),
]
_PytestWorkersOption = typ.Annotated[
    str | None,
    typer.Option(
        help=(
            "Worker count for pytest-xdist (-n). Use a positive integer, "
            '"auto", "logical", or "" to disable parallelism. Defaults to '
            '"auto".'
        ),
    ),
]


def main(
    output_path: _OutputPathOption = None,
    lang: _LangOption = None,
    fmt: _FmtOption = None,
    github_output: _GithubOutputOption = None,
    baseline_file: _BaselineFileOption = None,
    pytest_workers: _PytestWorkersOption = None,
) -> None:
    """Run slipcover coverage and write the result to ``GITHUB_OUTPUT``."""
    out, fmt, github_output = _resolve_inputs(output_path, lang, fmt, github_output)
    try:
        workers = _resolve_pytest_workers(pytest_workers)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if workers:
        typer.echo(f"Pytest workers: {workers} (parallel via pytest-xdist)")
    else:
        typer.echo("Pytest workers: disabled (serial pytest run)")
    out.parent.mkdir(parents=True, exist_ok=True)
    percent = _run_coverage(fmt, out, workers)
    typer.echo(f"Current coverage: {percent}%")
    previous = read_previous_coverage(baseline_file)
    if previous is not None:
        typer.echo(f"Previous coverage: {previous}%")
    _emit_github_output(out, percent, github_output)


if __name__ == "__main__":
    typer.run(main)
