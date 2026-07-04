#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "plumbum>=1.8,<3.0"]
# ///

"""Run mutmut for a Python project and post the job summary.

Runs mutation testing with `mutmut <https://mutmut.readthedocs.io/>`_ in
the caller's uv-managed project, optionally scoped to recently changed
files, then parses ``mutmut results --all true`` into outcome counts and
a survivors table appended to ``GITHUB_STEP_SUMMARY``.

Behavioural notes (validated empirically against mutmut 3.6.0)
---------------------------------------------------------------
- ``mutmut run`` exits 0 even when mutants survive, so no exit-code
  masking is needed; a failing baseline exits non-zero and fails the
  step naturally.
- Positional run arguments are mutant-name globs in module-path form
  (file paths are rejected), so changed files are translated to module
  globs: ``src/pkg/mod.py`` becomes ``pkg.mod.*``.
- Results live in per-source ``.meta`` files under ``mutants/``; the
  parseable interface is ``mutmut results --all true``, which prints
  ``name: status`` lines.

The caller must be a uv-managed project with ``[tool.mutmut]`` configured
in ``pyproject.toml`` (``source_paths``, test selection, and runner).

Environment Variables
---------------------
INPUT_FILES : str, optional
    Space-separated changed Python files; empty means a full run.
INPUT_MUTMUT_VERSION : str, optional
    mutmut version to inject via ``uv run --with``. Default: ``3.6.0``.
INPUT_MODULE_PREFIX_STRIP : str, optional
    Path prefix stripped before module translation. Default: ``src/``.
INPUT_EXTRA_ARGS : str, optional
    Extra ``mutmut run`` arguments (shell-lexed).
INPUT_RESULTS_FILE : str, optional
    File receiving the raw results listing (artefact material).
    Default: ``mutation-mutmut-results.txt``.
GITHUB_STEP_SUMMARY : str
    Path of the job-summary file.

Usage
-----
As a workflow step::

    - run: uv run --script workflow_scripts/mutation_run_mutmut.py
      env:
        INPUT_FILES: ${{ steps.detect.outputs.root_files }}
"""

from __future__ import annotations

import collections
import dataclasses
import os
import shlex
import typing as typ
from pathlib import Path

from cyclopts import App, Parameter
from plumbum import RETCODE, local

if __package__:
    from .output import emit, fail
else:
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

app = App()

#: Statuses that indicate the suite failed to kill a runnable mutant.
SURVIVOR_STATUSES: frozenset[str] = frozenset({"survived", "no tests"})


@dataclasses.dataclass(frozen=True, slots=True)
class MutantResult:
    """One mutant's outcome from ``mutmut results --all true``.

    Attributes
    ----------
    name : str
        Fully qualified mutant name
        (``pkg.mod.x_func__mutmut_N``).
    status : str
        Outcome status (``killed``, ``survived``, ``no tests``, ...).
    """

    name: str
    status: str


def files_to_module_globs(files: str, prefix_strip: str) -> list[str]:
    """Translate changed Python files into mutmut mutant-name globs.

    mutmut 3.x rejects file paths as run arguments; scoping uses mutant
    names in module-path form instead.

    Parameters
    ----------
    files : str
        Space-separated file paths.
    prefix_strip : str
        Path prefix (e.g. ``src/``) removed before translation.

    Returns
    -------
    list[str]
        De-duplicated module globs such as ``pkg.mod.*``; ``__init__.py``
        maps to its package. Non-Python paths are ignored.
    """
    globs: list[str] = []
    for name in files.split():
        if not name.endswith(".py"):
            continue
        trimmed = name.removeprefix(prefix_strip).removesuffix(".py")
        parts = [part for part in trimmed.split("/") if part]
        if parts and parts[-1] == "__init__":
            parts.pop()
        if not parts:
            continue
        glob = ".".join(parts) + ".*"
        if glob not in globs:
            globs.append(glob)
    return globs


def parse_results(text: str) -> list[MutantResult]:
    """Parse ``mutmut results --all true`` output into mutant results.

    Parameters
    ----------
    text : str
        Raw command output; result lines have the form
        ``    name: status``.

    Returns
    -------
    list[MutantResult]
        Parsed results, in output order. Lines that do not look like
        mutant results (warnings, blank lines) are ignored.
    """
    results: list[MutantResult] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        name, separator, status = line.partition(": ")
        if not separator or " " in name or "__mutmut_" not in name:
            continue
        results.append(MutantResult(name=name, status=status.strip()))
    return results


def count_statuses(results: list[MutantResult]) -> dict[str, int]:
    """Count results per status, preserving first-seen order."""
    counts: collections.Counter[str] = collections.Counter()
    for result in results:
        counts[result.status] += 1
    return dict(counts)


def render_summary(results: list[MutantResult]) -> str:
    """Render mutmut results as job-summary Markdown."""
    counts = count_statuses(results)
    lines = ["## Mutation testing results (mutmut)", ""]
    if not results:
        lines.extend(("No mutants were tested.", ""))
        return "\n".join(lines)
    lines.extend(f"- **{status}:** {count}" for status, count in sorted(counts.items()))
    lines.append("")
    survivors = [r for r in results if r.status in SURVIVOR_STATUSES]
    if survivors:
        lines.extend(
            (
                "### Surviving mutants",
                "",
                "Inspect a survivor with `uv run mutmut show <name>`.",
                "",
                "| Mutant | Status |",
                "| ------ | ------ |",
            )
        )
        lines.extend(f"| `{r.name}` | {r.status} |" for r in survivors)
        lines.append("")
    return "\n".join(lines)


def _mutmut_command(version: str) -> list[str]:
    """Return the uv argument list prefix for a pinned mutmut."""
    return ["run", "--with", f"mutmut=={version}", "mutmut"]


@app.default
def main(
    *,
    files: typ.Annotated[str, Parameter(env_var="INPUT_FILES")] = "",
    mutmut_version: typ.Annotated[
        str, Parameter(env_var="INPUT_MUTMUT_VERSION")
    ] = "3.6.0",
    module_prefix_strip: typ.Annotated[
        str, Parameter(env_var="INPUT_MODULE_PREFIX_STRIP")
    ] = "src/",
    extra_args: typ.Annotated[str, Parameter(env_var="INPUT_EXTRA_ARGS")] = "",
    results_file: typ.Annotated[
        str, Parameter(env_var="INPUT_RESULTS_FILE")
    ] = "mutation-mutmut-results.txt",
) -> None:
    """Run mutmut, then parse and publish its results.

    Parameters
    ----------
    files : str
        Space-separated changed Python files; empty runs everything.
    mutmut_version : str
        mutmut version injected via ``uv run --with``.
    module_prefix_strip : str
        Path prefix stripped before module-glob translation.
    extra_args : str
        Extra ``mutmut run`` arguments (shell-lexed).
    results_file : str
        Destination for the raw results listing.

    Raises
    ------
    SystemExit
        Exits with mutmut's code when ``mutmut run`` fails (failing
        baseline, usage error), and with 1 when ``GITHUB_STEP_SUMMARY``
        is unset.
    """
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_env:
        fail("GITHUB_STEP_SUMMARY is not set")

    globs = files_to_module_globs(files, module_prefix_strip)
    if files.strip() and not globs:
        emit("mutation_mutmut_outcome", "no python files in scope")
        Path(results_file).write_text("", encoding="utf-8")
        return

    run_arguments = [*_mutmut_command(mutmut_version), "run"]
    run_arguments.extend(shlex.split(extra_args))
    run_arguments.extend(globs)
    emit("mutation_mutmut_command", ["uv", *run_arguments])
    code = local["uv"][run_arguments] & RETCODE(FG=True)
    emit("mutation_mutmut_exit_code", code)
    if code != 0:
        fail(f"mutmut run failed with exit code {code} (failing baseline?)")

    results_text = local["uv"][
        *_mutmut_command(mutmut_version), "results", "--all", "true"
    ]()
    Path(results_file).write_text(results_text, encoding="utf-8")
    results = parse_results(results_text)
    with Path(summary_env).open("a", encoding="utf-8") as handle:
        handle.write(render_summary(results))
    emit("mutation_mutmut_counts", count_statuses(results))


if __name__ == "__main__":
    app()
