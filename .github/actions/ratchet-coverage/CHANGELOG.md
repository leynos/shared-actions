# Changelog

## v1.0.4

- Switch to `cargo-llvm-cov` for coverage generation.

## v1.0.3

- Remove Linux-only gating so the action runs on all runners.

## v1.0.2

- Skip gracefully on non-Linux runners.

## v1.0.1

- Add `args` input and include it in tarpaulin command.
- Validate numeric coverage values before comparison.
- Handle integer coverage values in output parsing.

## v1.0.0

- Initial version with caching and baseline ratcheting.
