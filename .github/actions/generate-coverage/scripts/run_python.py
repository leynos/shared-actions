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
#: Where the "Resolve coverage interpreter" step publishes its choice. Read
#: once, in :func:`main`, and passed down as ``interpreter``; empty keeps uv's
#: own discovery, which is what a direct script run outside the action gets.
COVERAGE_PYTHON_ENV = "GC_COVERAGE_PYTHON"

SLIPCOVER_ARGS: tuple[str, ...] = ("-m", "slipcover")
SLIPCOVER_BRANCH_ARG = "--branch"
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


def _venv_args(interpreter: str) -> list[str]:
    """Return the ``uv venv`` arguments, naming the resolved interpreter.

    Examples
    --------
    >>> _venv_args("/opt/py/bin/python3.13")
    ['venv', '--python', '/opt/py/bin/python3.13', '.venv-coverage']
    >>> _venv_args("")
    ['venv', '.venv-coverage']
    """
    interpreter = interpreter.strip()
    python_args = ["--python", interpreter] if interpreter else []
    return ["venv", *python_args, str(COVERAGE_VENV)]


def _remove_coverage_venv() -> None:
    """Remove the coverage venv directory or non-directory placeholder.

    Uses ``shutil.rmtree`` for directories and ``Path.unlink`` for any
    other filesystem object (e.g. a symlink or a stale file).
    """
    if COVERAGE_VENV.is_dir() and not COVERAGE_VENV.is_symlink():
        shutil.rmtree(COVERAGE_VENV)
    else:
        COVERAGE_VENV.unlink(missing_ok=True)


def _recreate_coverage_venv(interpreter: str = "") -> Path:
    """Remove any existing broken venv, create a fresh one, and return its Python.

    ``interpreter`` is the resolved Python to build the venv on; empty keeps
    uv's own discovery.

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
    run_cmd(uv[*_venv_args(interpreter)])
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


def _acquire_coverage_python(interpreter: str = "") -> Path:
    """Discover or create the coverage venv and return its Python path.

    ``interpreter`` is passed to :func:`_recreate_coverage_venv` when a venv
    has to be created.

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
        python = _recreate_coverage_venv(interpreter)
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


def _ensure_coverage_venv(interpreter: str = "") -> str:
    """Create or repair the coverage venv and install project/test tooling.

    ``interpreter`` is the resolved Python the venv is built on, if any.

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
    python = _acquire_coverage_python(interpreter)
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


# _coverage_python_cmd() is memoized with lru_cache rather than using a
# mutable global.  GitHub Actions executes action steps sequentially in a
# single thread, so no synchronization is required; the cache is safe to
# use without a lock for the lifetime of this process.
@lru_cache(maxsize=1)
def _coverage_venv_python(interpreter: str = "") -> str:
    """Return the coverage venv interpreter, creating the venv on first use."""
    return _ensure_coverage_venv(interpreter)


def _coverage_child_path(interpreter: str = "") -> str:
    """Return ``PATH`` with the coverage environment's scripts directory first."""
    scripts_dir = str(Path(_coverage_venv_python(interpreter)).parent)
    inherited_path = os.environ.get("PATH", "")
    return os.pathsep.join(part for part in (scripts_dir, inherited_path) if part)


def coverage_child_env(interpreter: str = "") -> dict[str, str]:
    """Return the environment a coverage subprocess must run under.

    ``run_cmd`` re-applies the process environment to every command it runs, so
    a ``PATH`` bound to the command alone would be overwritten before the
    subprocess starts. The value therefore has to travel as the run's explicit
    environment.
    """
    return {**os.environ, "PATH": _coverage_child_path(interpreter)}


@lru_cache(maxsize=1)
def _coverage_python_cmd(interpreter: str = "") -> BoundCommand:
    """Return coverage Python with its scripts available to child processes."""
    python = _coverage_venv_python(interpreter)
    scripts_dir = str(Path(python).parent)
    # Two bounded lines, once per process: which interpreter runs the coverage
    # and which directory child executables resolve against. The composed PATH
    # itself is not reported, because the inherited half is unbounded.
    typer.echo(f"Coverage interpreter: {python}")
    typer.echo(f"Coverage scripts directory prepended to PATH: {scripts_dir}")
    return local[python].with_env(PATH=_coverage_child_path(interpreter))


_VALID_NAMED_WORKERS = frozenset({"auto", "logical"})


def _parse_pytest_workers(raw: str | None) -> str:
    """Parse and validate the pytest-workers value; raise ValueError on bad input.

    This function is pure: it performs no I/O and has no side-effects.
    Callers that need CLI error handling should use _normalize_pytest_workers.
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


def _normalize_pytest_workers(raw: str | None) -> str:
    """Validate and normalize the pytest-workers value, exiting on invalid input.

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


def _coverage_args(
    fmt: str,
    out: Path,
    workers: str = "",
    python_source: str = "",
) -> list[str]:
    """Return the slipcover/pytest argv for the requested format.

    A non-empty source scope is passed as one unchanged value before
    Slipcover's branch flag. Whitespace-only scopes are treated as unset.
    """
    args: list[str] = [*SLIPCOVER_ARGS]
    if python_source.strip():
        args.extend(["--source", python_source])
    args.append(SLIPCOVER_BRANCH_ARG)
    if fmt == "cobertura":
        # slipcover treats --xml as a boolean flag; --out sets the report path
        args.extend(["--xml", "--out", str(out)])
    args.extend(PYTEST_ARGS)
    if workers:
        args.extend(["-n", workers])
    return args


def coverage_cmd_for_fmt(
    fmt: str,
    out: Path,
    workers: str = "",
    python_source: str = "",
    interpreter: str = "",
) -> BoundCommand:
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
    python_source : str
        Slipcover source scope. Empty and whitespace-only values leave
        Slipcover's automatic source discovery in place; any other value is
        passed through unchanged as one ``--source`` argument before
        ``--branch``, so a comma-separated scope stays a single value.
    interpreter : str
        Resolved Python the coverage venv is built on; empty keeps uv's own
        discovery.

    Returns
    -------
    plumbum.commands.base.BoundCommand
        A plumbum command that runs slipcover via the coverage venv Python.
    """
    python_cmd = _coverage_python_cmd(interpreter)
    scope = "configured" if python_source.strip() else "default"
    # The decision, not the value: the scope reaches the log once already,
    # inside the command line the command logger reports. A bounded state line
    # distinguishes a deliberate scope from the Slipcover default at a glance.
    typer.echo(f"Coverage command: format={fmt}, source scope={scope}")
    return python_cmd[_coverage_args(fmt, out, workers, python_source)]


@contextlib.contextmanager
def tmp_coveragepy_xml(out: Path, interpreter: str = "") -> cabc.Generator[Path]:
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
    python_cmd = _coverage_python_cmd(interpreter)
    try:
        cmd = python_cmd["-m", "coverage", "xml", "-o", str(xml_tmp)]
        run_cmd(cmd, env=coverage_child_env(interpreter))
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


def _run_coverage(
    fmt: str,
    out: Path,
    workers: str = "",
    python_source: str = "",
    interpreter: str = "",
) -> str:
    """Run slipcover and return the line coverage percentage.

    Parameters
    ----------
    fmt : str
        Coverage format identifier passed to :func:`coverage_cmd_for_fmt`.
    out : Path
        Destination path for the coverage output file.
    workers : str
        Worker count for pytest-xdist's ``-n`` flag; empty disables xdist.
    python_source : str
        Slipcover source scope passed to :func:`coverage_cmd_for_fmt`; empty
        and whitespace-only values leave source discovery automatic.
    interpreter : str
        Resolved Python the coverage venv is built on; empty keeps uv's own
        discovery.

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
        cmd = coverage_cmd_for_fmt(fmt, out, workers, python_source, interpreter)
        run_cmd(cmd, method="run_fg", env=coverage_child_env(interpreter))
    except ProcessExecutionError as exc:
        raise typer.Exit(code=exc.retcode or 1) from exc
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if fmt == "coveragepy":
        with tmp_coveragepy_xml(out, interpreter) as xml_tmp:
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


def _python_source_entries(raw: str) -> tuple[str, ...]:
    """Return the entries Slipcover will read from a ``--source`` value.

    Slipcover splits the value on commas and strips nothing, so the entries
    are returned exactly as it will see them. An empty or whitespace-only
    entry names no source directory and is refused rather than passed on. So
    is an entry with leading or trailing whitespace: ``femtologging, tests``
    names a directory called `` tests``, which Slipcover never finds, and
    accepting it would silently measure nothing for that path.

    Raises
    ------
    ValueError
        When any entry is empty, whitespace-only, or padded with whitespace.

    Examples
    --------
    >>> _python_source_entries("episodic,alembic")
    ('episodic', 'alembic')
    """
    entries = tuple(raw.split(","))
    if any(not entry.strip() for entry in entries):
        message = (
            f"Invalid python-source value: {raw!r}. Empty entries are not "
            "allowed; provide comma-separated repository-relative source "
            "directories."
        )
        raise ValueError(message)
    padded = [entry for entry in entries if entry != entry.strip()]
    if padded:
        message = (
            f"Invalid python-source value: {raw!r}. These entries have "
            f"surrounding whitespace: {', '.join(repr(e) for e in padded)}. "
            "Slipcover reads each entry exactly as written, so remove the "
            "spaces around the commas."
        )
        raise ValueError(message)
    return entries


def _sources_outside_repository(
    entries: tuple[str, ...], repository_root: Path
) -> tuple[str, ...]:
    """Return the entries that are absolute or resolve outside the repository.

    Slipcover resolves both the configured source and each candidate filename
    before deciding whether a module is instrumentable, so a ``..`` escape or
    a symlink pointing out of the repository would make a foreign
    environment's dependencies eligible for instrumentation again. Each entry
    is resolved against ``repository_root`` rather than the working
    directory. An absolute entry is refused even when it names a directory
    inside the repository, because the scope is documented as
    repository-relative and Slipcover would take it as given.

    This reads the filesystem: resolving follows symlinks.

    Raises
    ------
    OSError, RuntimeError
        When the root or an entry cannot be resolved; Python 3.12 raises
        ``RuntimeError`` for a symlink loop.

    Examples
    --------
    >>> _sources_outside_repository(("src", "../elsewhere", "/abs"), Path("/repo"))
    ('../elsewhere', '/abs')
    """
    root = repository_root.resolve()
    return tuple(
        entry
        for entry in entries
        if Path(entry).is_absolute()
        or not (root / entry).resolve().is_relative_to(root)
    )


def _resolve_python_source(python_source: str | None, repository_root: Path) -> str:
    """Resolve the optional Python source scope from the CLI or action env.

    The raw non-empty value is preserved so a comma-separated Slipcover source
    list reaches the subprocess as one argument. Empty and whitespace-only
    values disable source scoping. A non-empty value is validated against
    *repository_root* before any coverage environment or subprocess exists.

    Raises
    ------
    ValueError
        When the value contains an empty or padded entry, an absolute entry,
        an entry that resolves outside the repository through ``..`` or a
        symlink, or an entry that cannot be resolved at all.
    """
    if python_source is None:
        python_source = os.getenv("INPUT_PYTHON_SOURCE", "")
    if not python_source.strip():
        return ""
    entries = _python_source_entries(python_source)
    try:
        outside = _sources_outside_repository(entries, repository_root)
    except (OSError, RuntimeError) as error:
        message = (
            f"Invalid python-source value: {python_source!r}. Its entries could "
            f"not be resolved: {error}."
        )
        raise ValueError(message) from error
    if outside:
        message = (
            f"Invalid python-source value: {python_source!r}. Source directories "
            "must be repository-relative and resolve inside the repository; "
            f"these do not: {', '.join(outside)}."
        )
        raise ValueError(message)
    return python_source


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
_PythonSourceOption = typ.Annotated[
    str | None,
    typer.Option(
        help="Optional comma-separated Python source scope for Slipcover.",
    ),
]


def main(
    output_path: _OutputPathOption = None,
    lang: _LangOption = None,
    fmt: _FmtOption = None,
    github_output: _GithubOutputOption = None,
    baseline_file: _BaselineFileOption = None,
    pytest_workers: _PytestWorkersOption = None,
    python_source: _PythonSourceOption = None,
) -> None:
    """Run slipcover coverage and write the result to ``GITHUB_OUTPUT``."""
    out, fmt, github_output = _resolve_inputs(output_path, lang, fmt, github_output)
    try:
        workers = _resolve_pytest_workers(pytest_workers)
        source = _resolve_python_source(python_source, Path.cwd())
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if workers:
        typer.echo(f"Pytest workers: {workers} (parallel via pytest-xdist)")
    else:
        typer.echo("Pytest workers: disabled (serial pytest run)")
    out.parent.mkdir(parents=True, exist_ok=True)
    # The one read of the resolved interpreter; everything below takes it as a
    # parameter, so nothing further down depends on the ambient environment.
    interpreter = os.getenv(COVERAGE_PYTHON_ENV, "")
    percent = _run_coverage(fmt, out, workers, source, interpreter)
    typer.echo(f"Current coverage: {percent}%")
    previous = read_previous_coverage(baseline_file)
    if previous is not None:
        typer.echo(f"Previous coverage: {previous}%")
    _emit_github_output(out, percent, github_output)


if __name__ == "__main__":
    typer.run(main)
