# Changelog

All notable changes to the `upload-release-assets` action will be documented in
this file.

## Unreleased

- Never archive or reuse an interpreter-bound uv script environment. `uv run`
  builds each PEP 723 script environment under `environments-v2`, bound to the
  interpreter that built it. A cache restored on a runner whose Python patch
  version differs then fails before the first script runs: the Windows venv
  launcher exits 103 with "did not find executable", which stopped every Windows
  `build-release` lane on PR #412. `Setup uv` sets `enable-cache: false`; a
  separate `Prune restored uv cache` step runs `uv cache prune` before this
  action invokes `uv`, removing anything the calling workflow's own `Setup uv`
  restored into the shared cache directory.

## v1.0.0 (Unreleased)

- Initial release migrated from `leynos/netsuke`
- Discover and validate staged artefacts in a distribution directory
- Upload artefacts to GitHub releases via `gh` command-line interface (CLI)
- Dry-run mode for validating assets without uploading
- Support for binary, package, and checksum files
- Nested directory namespacing with `__` separator
- Added `clobber` input to control asset overwriting
- Added `uploaded-count` output for tracking processed assets
