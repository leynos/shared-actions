# Changelog

All notable changes to the `macos-package` action are documented in this file.

## [Unreleased]

- Never archive or reuse an interpreter-bound uv script environment. `uv run`
  builds each PEP 723 script environment under `environments-v2`, bound to the
  interpreter that built it. A cache restored on a runner whose Python patch
  version differs then fails before the first script runs: the Windows venv
  launcher exits 103 with "did not find executable", which stopped every Windows
  `build-release` lane on PR #412. `Setup uv` now sets `prune-cache: true`, so
  no environment is saved and the cache key moves off archives that already
  contain one, and a `Prune restored uv cache` step removes anything a calling
  workflow's own `Setup uv` restored into the shared cache directory before
  this action invokes `uv`.
- Initial implementation of the macOS packaging action.
- Harden payload staging to isolate intermediate artefacts and validate
  installation prefixes.
- Capture fallback build metadata separately from the package version and
  expose it as an action output.
- Centralize Cyclopts configuration, standardize error handling, and add
  behavioural and unit tests for the Python helper scripts.
