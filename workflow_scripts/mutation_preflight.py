#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "packaging>=24,<27", "plumbum>=1.8,<3.0"]
# ///
"""Validate a mutation lane's environment before it enumerates any mutant.

Every failure recorded in issue #591 happened before a mutant was exercised.
The copied sandbox lacked a file the suite reads, the selected interpreter fell
outside the project's ``requires-python``, or an external command the tests
shell out to was never installed. Each was repaired one ``also_copy`` entry at
a time, which is why the same class of failure kept returning.

The repair is a declaration the repository owns and this helper checks once:

- ``sandbox_paths`` — paths that must survive into mutmut's ``mutants/`` copy.
- ``require_commands`` — external commands (and optionally versions) the tests
  invoke.
- ``native`` — a locked native dependency whose interpreter support the run
  depends on.
- ``checks`` — bounded commands run with a declared working directory,
  environment and import root.

The declaration lives in ``[tool.mutation-preflight]`` in the caller's
``pyproject.toml``, or in ``.github/mutation-preflight.toml`` for a repository
without one. Declaring it in both places is refused rather than ranked.

Phases, in the order they run
-----------------------------
``manifest``
    The declaration parses, names only keys this helper understands, and asks
    for nothing its lane cannot check.
``interpreter``
    The project interpreter satisfies the project's ``requires-python``,
    judged by the rule cv005-contracts uses for the coverage clause (#565),
    and the helper's own runtime is reported separately and held to its own
    floor.
``native``
    A declared native dependency is the revision ``uv.lock`` pins, and the
    project interpreter is inside the range that revision supports. Support
    that cannot be established stays ``unknown``; it is never assumed.
``sandbox``
    Every declared path exists, stays inside the checkout, is not a build
    output, and is one mutmut will actually copy.
``commands``
    Every required command resolves, and reports the pinned version when one
    is declared.
``checks``
    Each bounded command succeeds from the working directory, environment and
    import root it declares.

Every phase emits one ``mutation_preflight_phase`` line, whether it succeeded,
failed, was skipped as not applicable, or could only be reported as unknown.
The run ends with ``mutation_preflight_result``, and writes a machine-readable
``mutation-preflight.json`` beside bounded logs even when no mutant exists yet.

Layout
------
This file is the entry point and nothing else. The phases live in the
``mutationpreflight`` package beside it, one module each, and are re-exported
here. Running this file by path still works, because ``uv run --script`` puts
its own directory on the import path.

Environment Variables
---------------------
INPUT_LANE : str, optional
    ``mutmut`` (default) or ``cargo``. Only the mutmut lane materialises a
    filtered sandbox, so ``sandbox_paths`` is refused on the cargo lane.
INPUT_PROJECT_PYTHON : str, optional
    The interpreter the caller selected for the project, such as ``3.13``.
INPUT_HELPER_PYTHON : str, optional
    The interpreter running this helper. Defaults to the running one.
INPUT_REPOSITORY : str, optional
    The checkout to validate. Defaults to the current directory.
INPUT_OUTPUT_DIR : str, optional
    Where the JSON report and bounded logs are written.
GITHUB_STEP_SUMMARY : str
    Path of the job-summary file, appended to when set.

Usage
-----
As a workflow step::

    - run: uv run --script workflow_scripts/mutation_preflight.py
      env:
        INPUT_LANE: mutmut
        INPUT_PROJECT_PYTHON: ${{ inputs.python-version }}
"""

from __future__ import annotations

if __package__:
    from .mutationpreflight import app, main
else:
    from mutationpreflight import app, main  # type: ignore[import-not-found]

__all__ = ["app", "main"]

if __name__ == "__main__":
    app()
