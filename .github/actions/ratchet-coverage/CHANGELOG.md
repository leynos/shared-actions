# Changelog

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
