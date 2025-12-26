# Changelog

All notable changes to the `upload-release-assets` action will be documented
in this file.

## v1.0.0 (Unreleased)

- Initial release migrated from `leynos/netsuke`
- Discover and validate staged artefacts in a distribution directory
- Upload artefacts to GitHub releases via `gh` CLI
- Dry-run mode for validating assets without uploading
- Support for binary, package, and checksum files
- Nested directory namespacing with `__` separator
- Added `clobber` input to control asset overwriting
- Added `uploaded-count` output for tracking processed assets
