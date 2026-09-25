#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum"]
# ///
"""Resolve the interpreter the Python coverage venv is built on.

A bare ``uv venv`` takes whatever interpreter uv discovers first, and which
one that is changed between uv releases: from uv 0.12.19 a pull request could
measure on 3.14 while ``main``'s ratchet baseline had been measured on 3.13,
and every pull request then failed the ratchet with no code change. This step
chooses the interpreter explicitly, in this order:

1. the action's ``python-version`` input;
2. ``UV_PYTHON``, when the caller already pins it;
3. the first entry of ``.python-version`` in the working directory;
4. the ``python3`` (or ``python``) the job put on ``PATH``, which is the
   interpreter ``actions/setup-python`` installs.

It then locates that interpreter through uv, installing it when uv cannot
find it, and publishes three step outputs: ``python``, the absolute path the
venv is built from; ``version``, its ``major.minor``; and
``baseline-segment``, ``py<major.minor>-``, which the ratchet baseline cache
key carries so a change of interpreter starts a fresh baseline instead of
comparing figures measured on different Pythons.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import os
import re
import sys
from pathlib import Path

from plumbum import local

#: The step's own marker for where a run's interpreter came from, logged so a
#: run shows which rule chose it.
SOURCES: tuple[str, ...] = ("input", "UV_PYTHON", ".python-version", "PATH")
VERSION_PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"
MAJOR_MINOR = re.compile(r"\d+\.\d+")

type Runner = cabc.Callable[[list[str]], tuple[int, str]]


class ResolutionError(RuntimeError):
    """Raised when no interpreter can be chosen or located."""


@dc.dataclass(frozen=True, slots=True)
class Choice:
    """An interpreter request and the rule that produced it."""

    spec: str
    source: str


def _first_entry(python_version_file: Path) -> str:
    r"""Return the first non-comment entry of a ``.python-version`` file.

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     path = Path(tmp) / ".python-version"
    ...     _ = path.write_text("# pinned\\n3.13\\n3.12\\n", encoding="utf-8")
    ...     _first_entry(path)
    '3.13'

    Raises
    ------
    ResolutionError
        When the file exists but cannot be read or is not UTF-8, so the step
        reports it instead of failing with a traceback.
    """
    if not python_version_file.is_file():
        return ""
    try:
        text = python_version_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        msg = f"could not read {python_version_file}: {error}"
        raise ResolutionError(msg) from error
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            return entry
    return ""


def choose_interpreter(
    env: cabc.Mapping[str, str], python_version_file: Path
) -> Choice:
    """Return the interpreter request, applying the resolution order.

    Raises
    ------
    ResolutionError
        When none of the four sources names an interpreter.

    Examples
    --------
    >>> choose_interpreter({"INPUT_PYTHON_VERSION": "3.13"}, Path("/nonexistent"))
    Choice(spec='3.13', source='input')
    >>> choose_interpreter({"GC_PATH_PYTHON": "/usr/bin/python3"}, Path("/nonexistent"))
    Choice(spec='/usr/bin/python3', source='PATH')
    """
    for name, source in (("INPUT_PYTHON_VERSION", "input"), ("UV_PYTHON", "UV_PYTHON")):
        value = env.get(name, "").strip()
        if value:
            return Choice(value, source)
    entry = _first_entry(python_version_file)
    if entry:
        return Choice(entry, ".python-version")
    on_path = env.get("GC_PATH_PYTHON", "").strip()
    if on_path:
        return Choice(on_path, "PATH")
    msg = (
        "no interpreter for the coverage venv: set the python-version input, "
        "UV_PYTHON or .python-version, or put python3 on PATH"
    )
    raise ResolutionError(msg)


def find_interpreter(spec: str, run: Runner) -> Path | None:
    """Return the absolute path uv finds for ``spec``, or ``None``.

    A query: it runs ``uv python find`` and nothing else, so it never
    downloads an interpreter.
    """
    code, out = run(["uv", "python", "find", spec])
    lines = out.strip().splitlines()
    return Path(lines[-1].strip()) if code == 0 and lines else None


def install_interpreter(spec: str, run: Runner) -> None:
    """Download ``spec`` into uv's managed interpreters.

    Raises
    ------
    ResolutionError
        When uv cannot install ``spec``.
    """
    code, _ = run(["uv", "python", "install", spec])
    if code != 0:
        msg = f"uv could not install an interpreter for {spec!r}"
        raise ResolutionError(msg)


def major_minor(python: Path, run: Runner) -> str:
    """Return ``python``'s ``major.minor`` as the interpreter itself reports it.

    Raises
    ------
    ResolutionError
        When the interpreter cannot be run or reports an unexpected version.
    """
    code, out = run([str(python), "-c", VERSION_PROBE])
    version = out.strip() if code == 0 else ""
    if not MAJOR_MINOR.fullmatch(version):
        msg = f"{python} did not report a major.minor version: {out!r}"
        raise ResolutionError(msg)
    return version


def baseline_segment(version: str) -> str:
    """Return the baseline cache key segment for an interpreter version.

    Examples
    --------
    >>> baseline_segment("3.13")
    'py3.13-'
    """
    return f"py{version}-"


def _run(command: list[str]) -> tuple[int, str]:
    """Run ``command`` and return its exit code and standard output."""
    program, *arguments = command
    code, out, err = local[program][arguments].run(retcode=None)
    if code != 0 and err:
        print(err, file=sys.stderr)
    return code, out


def resolve(env: cabc.Mapping[str, str], cwd: Path, run: Runner) -> dict[str, str]:
    """Return the step outputs for the interpreter the environment selects.

    uv is asked to find the interpreter first and to install it only when it
    cannot, so a runner that already has the requested Python downloads
    nothing.

    Raises
    ------
    ResolutionError
        When no interpreter is chosen, uv cannot install it, or uv still
        cannot find it after installing it.
    """
    choice = choose_interpreter(env, cwd / ".python-version")
    python = find_interpreter(choice.spec, run)
    if python is None:
        install_interpreter(choice.spec, run)
        python = find_interpreter(choice.spec, run)
    if python is None:
        msg = f"uv installed {choice.spec!r} but could not find it"
        raise ResolutionError(msg)
    version = major_minor(python, run)
    return {
        "python": str(python),
        "version": version,
        "baseline-segment": baseline_segment(version),
        "source": choice.source,
    }


def main(env: cabc.Mapping[str, str], cwd: Path, run: Runner) -> int:
    """Resolve the interpreter and publish it as step outputs.

    The environment, working directory and command runner are injected, so
    the entry point is exercised without touching the process environment.
    """
    try:
        outputs = resolve(env, cwd, run)
    except ResolutionError as error:
        print(f"::error title=generate-coverage interpreter::{error}", file=sys.stderr)
        return 1
    print(
        "::notice title=generate-coverage interpreter::"
        f"python={outputs['version']} source={outputs['source']}"
    )
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        handle.writelines(f"{name}={value}\n" for name, value in outputs.items())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.environ, Path.cwd(), _run))
