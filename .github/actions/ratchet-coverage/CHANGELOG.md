# Changelog

## Unreleased

- Pin every `actions/cache` reference to the v6.1.0 commit
  `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` in place of the moving `v4` tag.
  The tag breaks the repository's SHA-pinning policy, and the older releases it
  can resolve to are not intercepted by a transparent runner cache, so their
  saves became wasted upload.
- Stop the ratchet baseline freezing after its first write. "Restore baseline"
  and "Save baseline" both used the full `actions/cache` action against the
  constant key `ratchet-baseline-<os>`, so the key had two writers and, being
  immutable, could only ever be written once. The pair now uses the
  `actions/cache/restore` and `actions/cache/save` sub-actions against a
  run-scoped key, with a shared `ratchet-baseline-<os>-` prefix as the
  restore-key, so every run persists a fresh baseline that later runs recover.

## v1.0.6

- Overwrite existing `cargo-llvm-cov` installation using `--force` to avoid
  failures when the binary is restored from cache.

## v1.0.5

- Round coverage values to two decimals before comparison to avoid failures from
  minor floating-point differences.
- Provide clearer error messages when `cargo` commands fail.

## v1.0.4

- Switch to `cargo-llvm-cov` for coverage generation.

## v1.0.3

- Remove Linux-only gating, so the action runs on all runners.

## v1.0.2

- Skip gracefully on non-Linux runners.

## v1.0.1

- Add the `args` input and include it in the tarpaulin command.
- Validate numeric coverage values before comparison.
- Handle integer coverage values in output parsing.

## v1.0.0

- Initial version with caching and baseline ratcheting.
