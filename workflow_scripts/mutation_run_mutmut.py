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
  globs: ``src/pkg/mod.py`` becomes ``pkg.mod.*``. Test modules are not
  mutable source, so they are skipped; a scope of test files alone
  short-circuits to a graceful no-op rather than aborting mutmut with an
  empty filter.
- The reusable workflow's ``INPUT_*`` variables configure this script but
  are stripped from the environment before ``mutmut run``, so they cannot
  leak into the mutated project's in-process pytest baseline.
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
import re
import shlex
import sys
import typing as typ
from pathlib import Path, PurePosixPath

from cyclopts import App, Parameter
from plumbum import RETCODE, local

if typ.TYPE_CHECKING:
    import collections.abc as cabc

if __package__:
    from .output import emit, fail
else:
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

app = App()

#: Prefix of the workflow-input environment variables. mutmut runs the
#: caller's test suite in-process as its baseline, inheriting this
#: process's environment; leaving ``INPUT_*`` set would let workflow
#: inputs (for example ``INPUT_MODULE_PREFIX_STRIP``) leak into the
#: mutated project's tests, so they are stripped before ``mutmut run``.
WORKFLOW_INPUT_ENV_PREFIX = "INPUT_"

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


#: Stems marking a pytest test module: ``conftest``, ``test_*``, ``*_test``.
TEST_STEM_RE = re.compile(r"^(?:conftest|test_.*|.*_test)$")

#: Directory names that hold test modules.
TEST_DIR_NAMES: frozenset[str] = frozenset({"test", "tests"})


def _is_test_file(path: PurePosixPath) -> bool:
    """Return True when ``path`` is a pytest test module, not source.

    mutmut only generates mutants for source files, so a change window of
    test modules alone maps to no mutants and would abort ``mutmut run``
    with an empty filter. Files whose stem matches ``TEST_STEM_RE`` or
    that sit under a ``test``/``tests`` directory are treated as
    non-mutable and skipped.
    """
    if TEST_STEM_RE.match(path.stem):
        return True
    return any(part in TEST_DIR_NAMES for part in path.parts[:-1])


def _module_glob_for(name: str, prefix_strip: str) -> str | None:
    """Translate one changed file into a mutant-name glob, or None.

    ``__init__.py`` maps to its package; non-Python paths, test modules,
    and paths that reduce to nothing after stripping yield None.
    """
    path = PurePosixPath(name)
    if path.suffix != ".py":
        return None
    if _is_test_file(path):
        return None
    stripped_base = PurePosixPath(prefix_strip.rstrip("/")) if prefix_strip else None
    if stripped_base is not None and path.is_relative_to(stripped_base):
        path = path.relative_to(stripped_base)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return None
    return ".".join(parts) + ".*"


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
    globs = (_module_glob_for(name, prefix_strip) for name in files.split())
    return list(dict.fromkeys(glob for glob in globs if glob is not None))


def _parse_result_line(line: str) -> MutantResult | None:
    """Parse one output line into a mutant result, or None for noise.

    A result line has the form ``name: status`` where ``name`` is a
    space-free mutant name containing the ``__mutmut_`` marker; anything
    else (warnings, blank lines, progress output) is noise.
    """
    name, separator, status = line.partition(": ")
    if not separator:
        return None
    is_mutant_name = " " not in name and "__mutmut_" in name
    if not is_mutant_name:
        return None
    return MutantResult(name=name, status=status.strip())


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
    parsed = (_parse_result_line(raw_line.strip()) for raw_line in text.splitlines())
    return [result for result in parsed if result is not None]


def count_statuses(results: list[MutantResult]) -> dict[str, int]:
    """Count results per status, preserving first-seen order.

    Parameters
    ----------
    results : list[MutantResult]
        Parsed mutant results, as produced by ``parse_results``.

    Returns
    -------
    dict[str, int]
        Mapping of status name (``killed``, ``survived``, ...) to the
        number of mutants with that status, keyed in first-seen order.
    """
    counts: collections.Counter[str] = collections.Counter()
    for result in results:
        counts[result.status] += 1
    return dict(counts)


def _render_survivor_table(survivors: list[MutantResult]) -> list[str]:
    """Render the surviving-mutants table lines (empty when none)."""
    if not survivors:
        return []
    header = [
        "### Surviving mutants",
        "",
        "Inspect a survivor with `uv run mutmut show <name>`.",
        "",
        "| Mutant | Status |",
        "| ------ | ------ |",
    ]
    rows = [f"| `{r.name}` | {r.status} |" for r in survivors]
    return [*header, *rows, ""]


def render_summary(results: list[MutantResult]) -> str:
    """Render mutmut results as job-summary Markdown.

    Parameters
    ----------
    results : list[MutantResult]
        Parsed mutant results, as produced by ``parse_results``.

    Returns
    -------
    str
        Markdown with per-status counts and, where mutants survived or
        lacked covering tests, a table naming each such mutant. When
        ``results`` is empty, an explanatory message is returned
        instead.
    """
    lines = ["## Mutation testing results (mutmut)", ""]
    if not results:
        return "\n".join((*lines, "No mutants were tested.", ""))
    counts = count_statuses(results)
    lines.extend(f"- **{status}:** {count}" for status, count in sorted(counts.items()))
    lines.append("")
    survivors = [r for r in results if r.status in SURVIVOR_STATUSES]
    lines.extend(_render_survivor_table(survivors))
    return "\n".join(lines)


def _mutmut_command(version: str) -> list[str]:
    """Return the uv argument list prefix for a pinned mutmut."""
    return ["run", "--with", f"mutmut=={version}", "mutmut"]


def _workflow_input_env_names(names: cabc.Iterable[str]) -> list[str]:
    """Return the workflow-input variable names among ``names``.

    These are the ``INPUT_*`` variables the reusable workflow sets on the
    run step; they configure this script but must not reach the mutated
    project's test environment.
    """
    return [name for name in names if name.startswith(WORKFLOW_INPUT_ENV_PREFIX)]


def _run_mutation_testing(
    globs: list[str], mutmut_version: str, extra_args: str
) -> None:
    """Run ``mutmut run``, propagating any non-zero exit code.

    mutmut exits 0 even when mutants survive, so a non-zero code means a
    failing baseline or a usage error; the step fails with mutmut's own
    exit code.
    """
    run_arguments = [
        *_mutmut_command(mutmut_version),
        "run",
        *shlex.split(extra_args),
        *globs,
    ]
    emit("mutation_mutmut_command", ["uv", *run_arguments])
    # mutmut runs the caller's test suite in-process as its baseline,
    # inheriting the subprocess environment; strip the workflow's INPUT_*
    # variables so they cannot leak into the mutated project's tests
    # (shared-actions#369). The `with` block restores the environment on
    # exit, leaving the later `mutmut results` call unaffected.
    with local.env():
        for name in _workflow_input_env_names(list(local.env.keys())):
            del local.env[name]
        code = local["uv"][run_arguments] & RETCODE(FG=True)
    emit("mutation_mutmut_exit_code", code)
    if code != 0:
        emit(
            "mutation_mutmut_error",
            f"mutmut run failed with exit code {code} (failing baseline?)",
            stream=sys.stderr,
        )
        raise SystemExit(code)


NO_SOURCE_SUMMARY = """## Mutation testing results (mutmut)

Skipped: changed files contain no mutable source.
"""


def _write_no_source_summary(summary_path: str) -> None:
    """Note a graceful no-op in the job summary when nothing is mutable.

    Reached when the changed-file scope contains only non-Python or test
    files, so no mutant-name globs are produced; skipping keeps ``mutmut
    run`` from aborting on an empty filter (agent-helper-scripts#74).
    """
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(NO_SOURCE_SUMMARY)


def _publish_results(mutmut_version: str, results_file: str, summary_path: str) -> None:
    """Capture ``mutmut results``, then write the artefact and summary."""
    results_text = local["uv"][
        *_mutmut_command(mutmut_version), "results", "--all", "true"
    ]()
    Path(results_file).write_text(results_text, encoding="utf-8")
    results = parse_results(results_text)
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(render_summary(results))
    emit("mutation_mutmut_counts", count_statuses(results))


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
        emit("mutation_mutmut_outcome", "changed files contain no mutable source")
        Path(results_file).write_text("", encoding="utf-8")
        _write_no_source_summary(summary_env)
        return

    _run_mutation_testing(globs, mutmut_version, extra_args)
    _publish_results(mutmut_version, results_file, summary_env)


if __name__ == "__main__":
    app()
