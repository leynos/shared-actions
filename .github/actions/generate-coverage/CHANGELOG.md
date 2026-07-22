# Changelog

## Unreleased

- Stop masking coverage failures with an empty-artefact-name error. The
  "Archive coverage" step runs with `if: always()`, but the step that computes
  its artefact name previously did not, so any earlier failure (for example a
  tripped ratchet gate) skipped the name computation and the upload failed with
  a confusing empty-artefact-name error that hid the real cause. The
  name-computing step now also runs with `if: always()`, and the upload falls
  back to a run-scoped name if it still cannot be computed, so a failing run
  surfaces its real error and still archives its coverage report.

- Add a `language` input (`auto`, `rust`, `python`, `mixed`; default `auto`) to
  force the coverage scope. `auto` preserves the existing manifest-based
  detection. Explicit values fail fast when their prerequisites are absent:
  `rust` requires a resolved Cargo manifest and ignores a configuration-only
  `pyproject.toml` (no `[project]` table); `python` requires a syncable
  `pyproject.toml` with a `[project]` table, matching the action's `uv sync`
  contract; `mixed` requires both. This lets a Rust-only repository that keeps a
  tooling-only `pyproject.toml` (for Ruff, Pylint, ty, etc.) set `language: rust`
  and keep generating `lcov`, which `auto` would otherwise reject by classifying
  the repository as mixed. Callers that omit `language` are unaffected.

- Fix the coverage ratchet baseline freeze. The "Save baselines" step wrote a
  constant, run-id-less cache key (`ratchet-baseline-<os>`) guarded by
  `cache-hit != 'true'`, while "Restore baselines" recovered a run-id-suffixed
  key (`ratchet-baseline-<os>-<run_id>`) via a prefix restore-key. Because
  GitHub Actions cache entries are immutable, the constant key could only be
  written once and then froze the baseline until the 7-day eviction, so the
  ratchet never advanced when coverage improved and false-tripped "Coverage
  decreased" on pull requests for repositories with any coverage
  nondeterminism. The save step now uses the same run-id-suffixed key as the
  restore step's primary key and drops the `cache-hit` guard, so every main run
  persists a fresh baseline that later runs recover via the restore-key prefix
  (newest matching entry wins). No inputs change.
- Add a provisional symmetric +/-1 percentage-point dead-band to the coverage
  ratchet comparison (`ratchet_coverage.py`). Coverage within one absolute
  percentage point of the stored baseline is treated as noise: the run passes
  and the baseline is held. A drop of more than one point below the baseline
  still fails ("Coverage decreased"); a rise of more than one point above the
  baseline advances it. Holding the baseline within the band prevents a
  nondeterministic low run from false-tripping the gate and a lucky-high run
  from inflating the baseline so the next normal run fails. The tolerance is a
  single named constant (`RATCHET_TOLERANCE_PP = 1.0`).
- Omit `--summary-only` from `cargo llvm-cov` for the file formats
  (`lcov`, `cobertura`). With the flag, cargo-llvm-cov exports only
  summary information, so reports lacked per-line execution records
  (LCOV `DA` lines, Cobertura `<line>` elements) and changed-line
  coverage gates (e.g. CodeScene) had nothing to evaluate. Streamed
  formats keep the flag so stdout remains parseable.
- Ensure a pinned `cargo-binstall` (`v1.19.1`) is present before installing
  Rust coverage tooling. The new "Ensure cargo-binstall" step verifies any
  existing binary against the pinned version and reuses it only on a match;
  otherwise it downloads the checksum-pinned installer script and verifies the
  freshly installed version. This keeps the `cargo-llvm-cov` and
  `cargo-nextest` installs — which shell out to `cargo binstall` — from
  relying on an unpinned or stale binary already on the runner.
- Run the Python coverage suite under `pytest-xdist` by default. The new
  `pytest-workers` input (default `auto`) is forwarded to slipcover's
  `pytest -n` flag; set it to `""` to restore serial execution. `pytest-xdist`
  is installed alongside `slipcover`, `pytest`, and `coverage`. Note that
  slipcover 1.0.18's xdist plugin drops `--omit` on worker processes — see
  README for the implication for projects with co-located in-package tests.

## v1.3.15 (2026-04-30)

- Preserve the `.venv-coverage/bin/python` path when installing coverage
  tooling. Linux venv Python executables are often symlinks to the base
  interpreter, and resolving the symlink made `uv pip install --python` target
  the externally managed system Python instead of the coverage venv.

## v1.3.14 (2026-04-28)

- Run Python coverage tooling in an isolated, job-local virtual environment
  (`.venv-coverage`) instead of using `uv run --with`. The venv is created once
  per process, reused across calls within the same job, and repaired
  automatically when the Python executable is missing.
- Project dependencies are synced into the venv via
  `uv sync --inexact --python`; `slipcover`, `pytest`, and `coverage` are
  installed via
  `uv pip install --python` without `--system`.
- Broken-venv recovery: if `.venv-coverage` exists but its Python executable
  is absent or a non-directory placeholder occupies its path, the directory is
  removed and recreated before proceeding.

## v1.3.13 (2026-04-16)

- Override Cranelift coverage builds via
  `CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm` and
  `CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm` so `cargo llvm-cov` child cargo
  processes inherit the LLVM backend.
- Remove the outer `cargo --config profile.*.codegen-backend="llvm"` prefix
  workaround from Rust coverage command construction.

## v1.3.12 (2026-02-18)

- Add optional `cargo-manifest` input for repositories where `Cargo.toml`
  lives outside the repository root.
- Detect Rust projects using root `Cargo.toml` first, then fall back to
  `cargo-manifest` when provided and present.
- Pass `--manifest-path <selected-manifest>` to `cargo llvm-cov` runs.

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
- Add optional `artefact-name-suffix` input, so callers can customize naming.
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
  the binary along with Cargo artefacts.

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
