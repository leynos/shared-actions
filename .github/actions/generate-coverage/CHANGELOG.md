# Changelog

## v1.3.11 (2026-01-12)

- Add `use-cargo-nextest` input (default true) and run Rust coverage via
  `cargo llvm-cov nextest` when enabled.
- Install `cargo-nextest` via cargo-binstall with pinned version and checksum
  verification; create a temporary nextest config when none is present.

## v1.3.10 (2025-11-11)

- Remove the step that attempted to ``uv pip install --system`` Python
  dependencies and instead run slipcover/pytest via ``uv run`` so the action
  works on environments where the system interpreter is marked as
  externally-managed (e.g. Ubuntu 24.04).

## v1.3.9 (2025-11-06)

- Include runner OS and architecture in uploaded coverage artefact names.
- Add optional `artefact-name-suffix` input, so callers can customise naming.
- Expose new `artefact-name` output for referencing archived coverage artefacts.

## v1.3.8 (2025-09-06)

- Invalidate dependency cache when the action version changes by using
  a `cache-suffix` in the `setup-uv` step. Applies even when
  `github.action_ref` is empty (local path usage) thanks to the `github.sha`
  fallback.

## v1.3.7

- Include job identifier and matrix index in the coverage artefact name to
  avoid collisions in matrix workflows.

## v1.3.6 (2025-07-28)

- Log the current coverage percentage after each run. When ratcheting is enabled
  and a baseline exists, the previous percentage is printed as well.
- Extracted baseline reading into a shared helper and improved error handling.

## v1.3.5

- Fix ratchet step ordering so coverage is checked after Python results are
  available.

## v1.3.4

- Force reinstall of `cargo-llvm-cov` so cached binaries don't cause the
  installation step to fail.

## v1.3.3 (2025-07-27)

- Install `cargo-llvm-cov` automatically when running Rust coverage and cache
  the
  binary along with Cargo artefacts.

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

- Support Python projects by running `slipcover` when `pyproject.toml` is
  present.
- Expose `file` and `format` outputs.
- Default coverage format changed to `cobertura`.
- Fail fast if both `Cargo.toml` and `pyproject.toml` exist.

## v1.0.0 (2025-06-20)

- Initial version using `cargo llvm-cov` for Rust projects.
