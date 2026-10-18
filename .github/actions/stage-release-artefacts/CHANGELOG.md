# Changelog

All notable changes to the `stage-release-artefacts` action will be documented
in this file.

## Unreleased

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

## v1.0.0 (Unreleased)

- Initial release migrated from `leynos/netsuke`
- Tom's Obvious, Minimal Language (TOML) based configuration for artefact
  staging
- Template variable substitution in paths
- Glob pattern support for source resolution
- Secure Hash Algorithm (SHA-256) checksum sidecar generation
- Optional artefacts with fallback alternatives
- Windows path normalization option
- Export staged paths and checksums as workflow outputs
- Optional cargo-binstall tar.gz archive staging with checksum and
  `binstall-archive-path` output
