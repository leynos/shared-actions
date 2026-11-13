# Changelog

All notable changes to the `install-prover-tools` action will be documented in
this file.

## Unreleased

- Add the action: install Kani, Verus and Z3 through a pinned
  `rust-prover-tools` revision (default `592558f7`), cache the digest-checked
  archives, and verify them again on every use.
- Add the `cache` input; `false` skips the archive caches for a job whose cold
  install costs less than moving them.
