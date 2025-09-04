# Changelog

## v1.3.7

- Include job identifier and matrix index in the coverage artifact name to
  avoid collisions in matrix workflows.

## v1.3.6 (2025-07-28)

- Log the current coverage percentage after each run. When ratcheting is enabled
  and a baseline exists, the previous percentage is printed as well.
- Extracted baseline reading into a shared helper and improved error handling.

## v1.3.5

- Fix ratchet step ordering so coverage is checked after Python results are available.

## v1.3.4

- Force reinstall of `cargo-llvm-cov` so cached binaries don't cause the
  installation step to fail.

## v1.3.3 (2025-07-27)

- Install `cargo-llvm-cov` automatically when running Rust coverage and cache the
  binary along with Cargo artifacts.

## v1.3.2 (2025-07-26)

- Pin `setup-uv` step to v6.4.3.

## v1.3.1 (2025-07-06)

- Parse coverage XML using `defusedxml` for better security.
- Fix formatting in the Python runner and improve Rust coverage parsing.

## v1.3.0 (2025-07-06)

- Add optional ratcheting support via `with-ratchet`. Coverage percentages for
  Rust and Python are tracked separately and compared against their respective
  baselines.
- Improve baseline caching to allow updates and consolidate ratcheting steps.

## v1.2.0 (2025-06-26)

- Support projects containing both Python and Rust. Cobertura reports from
  each language are merged using `uvx merge-cobertura`.

## v1.1.2 (2025-06-25)

- Merge detection and validation into a single step to simplify the workflow,
  routing the `lang` output directly from the `detect` step.
- Enable strict mode in the detection step and explicitly use Bash.

## v1.1.1 (2025-06-24)

- Automatically install `slipcover` and `pytest` using `setup-uv` when running
  Python coverage.

## v1.1.0 (2025-06-23)

- Support Python projects by running `slipcover` when `pyproject.toml` is present.
- Expose `file` and `format` outputs.
- Default coverage format changed to `cobertura`.
- Fail fast if both `Cargo.toml` and `pyproject.toml` exist.

## v1.0.0 (2025-06-20)

- Initial version using `cargo llvm-cov` for Rust projects.
