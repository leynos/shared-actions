# Changelog

All notable changes to the `install-prover-tools` action will be documented in
this file.

## Unreleased

- Add Kani snapshot support: a consumer holding `tools/kani/snapshot.toml`
  installs Kani through `prover-tools kani snapshot install` and `check` (inputs
  `kani-snapshot` and `github-token`; outputs `kani-snapshot` and
  `kani-snapshot-pin`). Without a pin the action is unchanged.
- Add the action: install Kani, Verus and Z3 through a pinned
  `rust-prover-tools` revision (default `592558f7`), cache the digest-checked
  archives, and verify them again on every use.
- Add the `cache` input; `false` skips the archive caches for a job whose cold
  install costs less than moving them.
