# Developer's Guide

This document describes the internal architecture of the `generate-coverage`
action, its public API, concurrency model, and Makefile tool-resolution
strategy.

## Python Coverage Venv Architecture

### Motivation

Earlier revisions of `run_python.py` invoked slipcover and coverage.py via
`uv run --with slipcover --with pytest --with coverage python ...`, which
re-resolved and reinstalled tooling on every invocation and could not cache the
interpreter reference within the same process.

### Lifecycle

`run_python.py` manages a dedicated throwaway virtual environment at
`.venv-coverage` in the working directory.

| Step | Function | Description |
| --- | --- | --- |
| 1 | `_find_coverage_python()` | Locate the Python executable. |
| 2 | `_remove_coverage_venv()` | Remove the venv or placeholder path. |
| 3 | `_recreate_coverage_venv()` | Recreate the venv. |
| 4 | `_ensure_coverage_venv()` | Sync project deps and install tooling. |
| 5 | `_coverage_python_cmd()` | Return the cached `plumbum.BoundCommand`. |

`_find_coverage_python()` returns `None` when `.venv-coverage` is absent, is a
symlink, is a non-directory, or lacks a Python executable. `_remove_coverage_venv()`
uses `shutil.rmtree` for directories and `Path.unlink` for files or symlinks.
`_recreate_coverage_venv()` raises `RuntimeError` if the executable is still
absent after creation. `_ensure_coverage_venv()` runs
`uv sync --inexact --python <venv_python>` and
`uv pip install --python <venv_python> slipcover pytest coverage`.
`<venv_python>` is the absolute path inside `.venv-coverage`; it is not
resolved through symlinks before being passed to uv.
`_coverage_python_cmd()` uses `@lru_cache(maxsize=1)` and returns the cached
command for `<venv_python>` thereafter.

### Public API

| Symbol | Signature | Role |
| --- | --- | --- |
| `coverage_cmd_for_fmt` | `(fmt, out)` | Build a slipcover command. |
| `tmp_coveragepy_xml` | `(out)` | Generate temporary Cobertura XML. |
| `main` | `(output_path, lang, fmt, github_output, baseline_file)` | Run. |

`coverage_cmd_for_fmt` returns a `BoundCommand` for the requested format.
`tmp_coveragepy_xml` yields a temporary XML path and removes it on exit.
`main` runs slipcover, parses coverage, and writes `GITHUB_OUTPUT`.

### Concurrency Model

`run_python.py` runs as a single-threaded GitHub Actions step. The
`@lru_cache(maxsize=1)` on `_coverage_python_cmd()` therefore requires no
explicit synchronization; the cache is safe for the lifetime of the process.

### Broken-Venv Recovery

If `.venv-coverage` is present but its Python executable is absent (or a
non-directory placeholder occupies its path), `_recreate_coverage_venv()`
removes the directory and recreates it from scratch. The case is detected by
`_find_coverage_python()` returning `None` when the directory already exists.

### POSIX and Windows Layouts

`_find_coverage_python()` checks three candidate paths in order:

- `COVERAGE_VENV/bin/python` (POSIX)
- `COVERAGE_VENV/Scripts/python.exe` (Windows)
- `COVERAGE_VENV/Scripts/python` (Windows without extension)

On POSIX, `bin/python` is commonly a symlink to the base interpreter, for
example `/usr/bin/python3.12`. `_find_coverage_python()` deliberately returns
`Path.absolute()` rather than `Path.resolve()` so the action passes
`.venv-coverage/bin/python` to uv. Resolving that symlink would make
`uv pip install --python` target the externally managed system interpreter
instead of the throwaway coverage venv.

The helper logs each candidate at DEBUG level with the candidate path, absolute
path, resolved target, file status, and symlink status. `_ensure_coverage_venv()`
logs the candidate set and the exact Python path passed to `uv sync` and
`uv pip install` at INFO level so CI logs can identify whether uv targeted the
venv path or the base interpreter.

## Makefile Tool Resolution

The `Makefile` resolves optional local tool installations before falling back
to bare names on `PATH`.

| Variable | Default resolution order |
| --- | --- |
| `UV` | `~/.local/bin/uv` if present, otherwise `uv` |
| `ACTION_VALIDATOR` | Bun install, then Cargo install, then `PATH` |
| `MDLINT` | `~/.bun/bin/markdownlint` if present, otherwise `markdownlint` |
| `MARKDOWNLINT_BASE` | `origin/main` |

`ACTION_VALIDATOR` resolves to `~/.bun/bin/action-validator`, then
`~/.cargo/bin/action-validator`, then `action-validator`. `MARKDOWNLINT_BASE`
is the base ref for `git diff` in the `markdownlint` target.

Override example:

```bash
make lint UV=uv MARKDOWNLINT_BASE=origin/develop
```

## Running the Test Suite

```bash
make test          # full suite
make check-fmt     # Ruff formatting check
make typecheck     # mypy
make lint          # Ruff lint + action-validator + markdownlint
```
