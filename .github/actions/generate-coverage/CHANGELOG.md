# Changelog

## v1.1.0
- Support Python projects by running `slipcover` when `pyproject.toml` is present.
- Expose `file` and `format` outputs.
- Default coverage format changed to `cobertura`.
- Fail if both `Cargo.toml` and `pyproject.toml` exist.

## v1.0.0
- Initial version using `cargo llvm-cov` for Rust projects.
