# Changelog

All notable changes to the `determine-release-modes` action will be documented
in this file.

## v1.0.0 (Unreleased)

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
- Initial release migrated from `leynos/netsuke`
- Normalize GitHub Actions event payloads into release workflow decision flags
- Support for `push`, `workflow_call`, and `pull_request` events
- Optional `dry-run` and `publish` input overrides
