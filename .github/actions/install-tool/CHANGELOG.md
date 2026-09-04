# Changelog

All notable changes to this action will be documented in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Install a pinned, digest-verified tool from `.github/tool-manifest.toml`.
  The manifest carries one entry per tool, version and target triple, with the
  archive URL, its SHA-256, the path of the binary inside it, and whether an
  upstream sidecar corroborated the digest. Seeded with cargo-audit 0.22.2,
  cargo-nextest 0.9.143, cargo-llvm-cov 0.9.0, cargo-dylint and dylint-link
  6.0.4, and sccache 0.17.0, across five targets.

  `version` is required and must name a manifest entry. There is no floating
  version and no lookup of a latest release: that is a network call in the
  critical path and a dependency that changes underneath you, which failed a
  job on PR #440 with `Unable to locate executable file: undefined`.

  The URL is recorded rather than derived from the triple, because asset naming
  does not follow it; sccache ships musl archives for every Linux target and
  cargo-nextest ships one universal archive for both Apple targets. The member
  path is recorded because upstreams disagree about whether the binary sits at
  the archive root or under one directory, and `--strip-components=1` is right
  for one shape and destroys the other.

  Verification is the pinned digest, then the member, then the installed
  binary's own version, which is asked on the cached path as well as the fresh
  one. `dylint-link` cannot be asked, because it refuses every argument without
  `RUSTUP_TOOLCHAIN`, so its entry says so and the run reports
  `install-tool.verify=unsupported` rather than claiming a check it did not
  make.

  Two limits are recorded in the manifest rather than left to be discovered.
  cargo-audit and cargo-llvm-cov publish no digest sidecars at all, so those
  pins carry `sidecar = "absent"` and have no upstream cross-check. Dylint
  6.0.4 ships Linux archives only, so macOS and Windows fail closed with the
  targets it does offer. Both are tracked in issue #450.

  The archive is hashed from stdin rather than by file name, because GNU
  `sha256sum` escapes its output line for a name containing a backslash and a
  Windows path therefore rejected a correct archive (#432). The extractor is
  chosen by asset extension and never by probing what `tar` resolves to, since
  Git Bash puts MSYS GNU tar ahead of the system bsdtar that can read a zip
  (#446). Every fragment is Bash 3.2 safe and none relies on an `ERR` trap, so
  each terminal path emits its own bounded metric.

  The action installs into `bin-dir` and archives nothing; a lane that wants an
  installed tool to survive between jobs owns that cache step and its key.
