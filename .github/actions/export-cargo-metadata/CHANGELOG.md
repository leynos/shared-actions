# Changelog

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

## v1.0.0 (2025-12-26)

- Initial release migrated from leynos/netsuke
- Extract package metadata (name, version, description, bin-name) from
  Cargo.toml (Tom's Obvious, Minimal Language)
- Support workspace version inheritance
- Export fields as both GitHub outputs and environment variables
- Binary name detection with [[bin]] fallback to [package].name
