# Changelog

All notable changes to this project will be documented in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Provide shared packaging fixtures and helpers that build the sample project once and produce `.deb` and `.rpm` artefacts for the integration tests.
- Support staging and packaging for `unknown-linux-musl` targets alongside GNU triples for x86_64, aarch64, i686, arm*, and riscv64 builds.

## [0.1.0] - 2025-09-10

### Added (0.1.0)

- Initial skeleton.
- Replace Bats smoke test with pytest version.
