# Changelog

## v1.1.0 (2025-06-23)
- Support Python projects by running `slipcover` when `pyproject.toml` is present.
- Expose `file` and `format` outputs.
- Default coverage format changed to `cobertura`.
- Fail fast if both `Cargo.toml` and `pyproject.toml` exist.

## v1.1.1 (2025-06-24)
- Automatically install `slipcover` and `pytest` using `setup-uv` when running
  Python coverage.

## v1.0.0 (2025-06-20)
- Initial version using `cargo llvm-cov` for Rust projects.
