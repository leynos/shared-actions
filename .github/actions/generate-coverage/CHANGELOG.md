# Changelog

## Unreleased

- Refuse a `publish-baseline` that is neither `auto` nor `always`, before the
  action does anything, rather than treating an unrecognized value as `auto`.

- Publish the ratchet baseline only on a push to `refs/heads/main`. The save
  step had no event guard, so a `workflow_dispatch` gathering warm-cache
  evidence published a new baseline instead of reading the generation it was
  measuring, and a push to any branch could advance the baseline that later
  pull requests are measured against. The new `publish-baseline` input takes
  `auto`, the guarded default, or `always` for a repository whose merges fire
  no push event or whose trunk is not called `main`.

- Keep `RUN_RUST_CARGO_WAIT_TIMEOUT` ahead of the new input. The input reaches
  the script under its own name, so a step no longer replaces a caller's
  job-level budget with this input's default.
- Refuse a watchdog budget that is not finite and greater than zero. Now
  that it is a public input, anything `float()` accepts could reach it: a `nan`
  budget never expires, an infinite one defers to the platform, and a
  non-positive one kills a healthy build immediately.

- Raise the cargo watchdog default from 600 to 1800 seconds and expose it as
  the `cargo-wait-timeout` input. The old figure suited a lane that restored a
  `target` archive; a lane that lets sccache own compiler output has the whole
  instrumented compile to do inside the budget on a cold store, and netsuke's
  first trunk run after that change was killed at 600 s having finished all
  2,790 tests at about 512 s.
- Report the budget as `cargo watchdog budget: <seconds>s` before cargo
  starts, and name both the budget and how to raise it when it expires. A cold
  build and a hang look identical from outside, and the message used to send
  the reader looking for a deadlock.

- Emit one stable metric line per ratchet cache outcome,
  `metric ratchet-cache.restore=<state>` and
  `metric ratchet-cache.save=<state>`, alongside the existing notice and job
  summary. The names are fixed and the values come from the closed outcome
  vocabularies, so a log scraper sees bounded cardinality and no cache key,
  path, or run identifier.
- Move the unpinned-cache-reference contract to a repository-wide test covering
  every action under `.github/actions`, which also requires all of them to
  share one pinned revision. The baseline cache lifecycle contract moves
  alongside it, parametrized over both actions that persist a ratchet baseline.

- Add `all-features`, `all-targets`, and `doctests` inputs so one coverage job
  can be a repository's only test execution. All three default to off.
  `all-features` supersedes `with-default-features` and is rejected alongside a
  non-empty `features` list. `all-targets` covers benches, examples, and every
  test target; doc tests are a separate target kind, so `doctests` runs
  `cargo test --doc --workspace` afterwards, uninstrumented, with the same
  feature selection.
- Stop every ratchet run failing its cache reservation. "Restore baselines"
  used the full `actions/cache` action, which registers a post-job save of its
  own, so it and the explicit "Save baselines" step both wrote the same
  run-id-suffixed key and each run logged
  `Failed to save: Unable to reserve cache ... already exists`. The two steps
  now use the `actions/cache/restore` and `actions/cache/save` sub-actions at
  one pinned revision, so exactly one step writes the key. The pair now reports
  bounded restore and save outcomes in the log and job summary, naming neither
  the key nor the baseline paths, and distinguishing a step skipped by an
  earlier failure from a ratchet that is switched off.
- Pin every `actions/cache` reference to the v6.1.0 commit
  `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` in place of the moving `v4` tag.
  The Cargo artefact and Python dependency caches used the tag, which breaks
  the repository's pinning policy and can resolve to releases a transparent
  runner cache does not intercept.
- Stop archiving the `target` tree. The Cargo cache now covers the Cargo
  binaries, registry, and Git index only; sccache carries compiler output.
- Add a `cache-provider` input. The default preserves the existing GitHub
  caches for uv, Cargo artefacts, and Python dependencies. Set it to `external`
  when the caller mounts those paths through one external cache owner, such as
  a Namespace cache volume; the action then disables its overlapping caches.
  Ratchet baseline caching remains unchanged.
- Preserve setup-uv's automatic GitHub-hosted versus self-hosted default and
  report bounded Cargo, Python, and uv cache outcomes in the log and job
  summary.
- Download `cargo-nextest` directly from its pinned official release and verify
  both the archive and executable SHA-256 digests. This removes
  cargo-binstall's QuickInstall substitution and any source-build fallback.
- Log bounded, structured events for the `cargo-nextest` download attempt and
  duration, the archive digest outcome, the executable digest outcome, and the
  install outcome.
- Separate the executable digest comparison, which is now pure and returns a
  `BinaryDigest`, from the reporting the orchestration performs
- Prepend the Cargo bin directory to `PATH` and `GITHUB_PATH` after installing
  a verified `cargo-nextest`, and fail when an unverified binary still shadows
  it, so later steps cannot run the binary that failed verification. Do the
  same when an already-installed binary is reused, so a custom `CARGO_HOME`
  whose `bin` directory is absent from `PATH` still resolves afterwards.
- Cap the `cargo-nextest` archive download at 200 MB and fail closed,
  deleting the partial file, once a response exceeds it. The digest check
  protects integrity, not disk space, and only runs after the whole response
  has landed, so an unbounded or redirected response could otherwise fill the
  runner's disk first.
- Report the `cargo-nextest` installation in the job summary, mirroring the
  Whitaker action, with a bounded set of `cargo-nextest.` metrics for the
  download outcome (duration and byte count), the archive digest outcome, the
  executable digest outcome, and the install outcome, including the reuse path.

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
  contract; `mixed` requires both. This lets a Rust-only repository that keeps
  a tooling-only `pyproject.toml` (for Ruff, Pylint, ty, etc.) set
  `language: rust` and keep generating `lcov`, which `auto` would otherwise
  reject by classifying the repository as mixed. Callers that omit `language`
  are unaffected.

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
  (`lcov`, `cobertura`). With the flag, cargo-llvm-cov exports only summary
  information, so reports lacked per-line execution records (LCOV `DA` lines,
  Cobertura `<line>` elements) and changed-line coverage gates (e.g. CodeScene)
  had nothing to evaluate. Streamed formats keep the flag so stdout remains
  parseable.
- Ensure a pinned `cargo-binstall` (`v1.19.1`) is present before installing
  Rust coverage tooling. The new "Ensure cargo-binstall" step verifies any
  existing binary against the pinned version and reuses it only on a match;
  otherwise it downloads the checksum-pinned installer script and verifies the
  freshly installed version. This keeps the `cargo-llvm-cov` install from
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
  installed via `uv pip install --python` without `--system`.
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
