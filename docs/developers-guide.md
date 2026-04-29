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
|---|---|---|
| 1 | `_find_coverage_python()` | Locate the Python executable within `.venv-coverage`; returns `None` if absent or if the venv directory is a symlink or non-directory. |
| 2 | `_remove_coverage_venv()` | Remove the venv directory (via `shutil.rmtree`) or any non-directory placeholder (via `Path.unlink`). |
| 3 | `_recreate_coverage_venv()` | Remove any broken venv state, create a fresh venv via `uv venv`, and return the new Python path. Raises `RuntimeError` if the executable is still absent after creation. |
| 4 | `_ensure_coverage_venv()` | Orchestrates steps 1-3, then runs `uv sync --inexact --python` and `uv pip install --python slipcover pytest coverage`. Returns the Python path as a string. |
| 5 | `_coverage_python_cmd()` | Calls `_ensure_coverage_venv()` on first use via `@lru_cache(maxsize=1)`; returns a cached `plumbum.BoundCommand` thereafter. |

### Public API

| Symbol | Signature | Role |
|---|---|---|
| `coverage_cmd_for_fmt` | `(fmt: str, out: Path) -> BoundCommand` | Build the slipcover command for a given format. |
| `tmp_coveragepy_xml` | `(out: Path) -> Generator[Path]` | Context manager: generate Cobertura XML via coverage.py, yield the path, clean up on exit. |
| `main` | `(output_path, lang, fmt, github_output, baseline_file)` | Entry point: run slipcover, parse coverage, write `GITHUB_OUTPUT`. |

### Concurrency Model

`run_python.py` runs as a single-threaded GitHub Actions step. The
`@lru_cache(maxsize=1)` on `_coverage_python_cmd()` therefore requires no
explicit synchronisation; the cache is safe for the lifetime of the process.

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

## Makefile Tool Resolution

The `Makefile` resolves optional local tool installations before falling back
to bare names on `PATH`.

| Variable | Default resolution order |
|---|---|
| `UV` | `~/.local/bin/uv` if present, otherwise `uv` |
| `ACTION_VALIDATOR` | `~/.bun/bin/action-validator`, then `~/.cargo/bin/action-validator`, then `action-validator` |
| `MDLINT` | `~/.bun/bin/markdownlint` if present, otherwise `markdownlint` |
| `MARKDOWNLINT_BASE` | `origin/main` (base ref for `git diff` in the `markdownlint` target) |

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
