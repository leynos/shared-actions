# Changelog

All notable changes to this project will be documented in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add `cache-provider` and `use-sccache` inputs, forwarded verbatim to the
  nested `setup-rust` step. The defaults (`github` and `true`) preserve the
  existing caching behaviour; `cache-provider: external` lets a caller that
  already owns the Cargo and uv paths, such as an Ubicloud or Namespace lane,
  avoid a second cache owner. An unrecognized `cache-provider` fails before
  toolchain setup.

- Add a `rustflags` input exported before the toolchain setup step so
  builds that require specific flags (for example `-Zpolonius=next`) are
  not stripped by the nested setup step's `-D warnings` default, which
  shadows the project's `build.rustflags` configuration. A pre-existing
  `RUSTFLAGS` environment variable still takes precedence.

- Cross-compile and stage `x86_64-unknown-illumos` artefacts from Linux runners.
- Provide shared packaging fixtures and helpers that build the sample project
  once and produce `.deb` and `.rpm` artefacts for the integration tests.
- Support staging and packaging for `unknown-linux-musl` targets alongside GNU
  triples for x86_64, aarch64, i686, arm*, and riscv64 builds.
- Require containerized `cross` builds for FreeBSD targets on non-FreeBSD hosts
  to enable `x86_64-unknown-freebsd` cross-compilation.
- Automatically export `CROSS_CONTAINER_ENGINE` for the detected container
  runtime when running FreeBSD builds with `cross`.
- Add a `manifest-path` input for selecting an alternate Cargo manifest.
- Add a `toolchain` input for explicitly overriding the resolved build
  toolchain.

### Changed

- Bump the nested `setup-rust` pin to
  `bffacaf91d3f3515110679a30fbf6dc781ddc549`, which carries the
  `cache-provider` and `use-sccache` inputs along with the Node.js 24
  dependency revisions. Cache paths, keys, and the effective `RUSTFLAGS`
  default are unchanged.

### Fixed

- Pin `setup-rust` to the commit behind `setup-rust-v1`, so toolchain inputs
  and OS guards apply when invoked from external repositories.
- Resolve toolchains from the target repository before falling back to the
  action's bundled default: explicit input first, then `rust-toolchain.toml` or
  `rust-toolchain`, then manifest `rust-version`.

## [0.1.0] - 2025-09-10

### Added (0.1.0)

- Initial skeleton.
- Replace Bats smoke test with pytest version.
