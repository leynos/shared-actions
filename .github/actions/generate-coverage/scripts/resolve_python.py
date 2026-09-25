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
    """
    if not python_version_file.is_file():
        return ""
    for line in python_version_file.read_text(encoding="utf-8").splitlines():
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


def locate(spec: str, run: Runner) -> Path:
    """Return the absolute interpreter path uv resolves ``spec`` to.

    uv is asked to find the interpreter first and to install it only when it
    cannot, so a runner that already has the requested Python downloads
    nothing.

    Raises
    ------
    ResolutionError
        When uv can neither find nor install ``spec``.
    """
    code, out = run(["uv", "python", "find", spec])
    if code != 0:
        install_code, _ = run(["uv", "python", "install", spec])
        if install_code == 0:
            code, out = run(["uv", "python", "find", spec])
    path = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if code != 0 or not path:
        msg = f"uv could not find or install an interpreter for {spec!r}"
        raise ResolutionError(msg)
    return Path(path)


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
    """Return the step outputs for the interpreter the environment selects."""
    choice = choose_interpreter(env, cwd / ".python-version")
    python = locate(choice.spec, run)
    version = major_minor(python, run)
    return {
        "python": str(python),
        "version": version,
        "baseline-segment": baseline_segment(version),
        "source": choice.source,
    }


def main() -> int:
    """Resolve the interpreter and publish it as step outputs."""
    try:
        outputs = resolve(os.environ, Path.cwd(), _run)
    except ResolutionError as error:
        print(f"::error title=generate-coverage interpreter::{error}", file=sys.stderr)
        return 1
    print(
        "::notice title=generate-coverage interpreter::"
        f"python={outputs['version']} source={outputs['source']}"
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        handle.writelines(f"{name}={value}\n" for name, value in outputs.items())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
