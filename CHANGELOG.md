# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Refactored `rust-build-release` command construction to assemble argv lists
  before handing commands to plumbum.
- Added Makefile tool discovery through candidate path lists for `RUFF` and
  `ACTION_VALIDATOR`.

### Added

- Added a cross command validation guard that rejects `+<toolchain>` overrides.
- Added the `rust-build-release` `test_commands.py` regression test module.
