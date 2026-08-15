# Developer's Guide

This document describes internal architecture and maintenance conventions for
the shared actions in this repository. It covers action-specific implementation
notes, public and internal APIs that affect contributors, concurrency
assumptions, and Makefile tool-resolution strategy.

## Spelling policy

Run `make spelling` to enforce en-GB-oxendict spelling. The dictionary-based
Typos scan checks tracked Markdown, while the phrase-correction check covers
the whole tracked repository, including Python, PowerShell, Rust and workflow
files. The generated and tracked `typos.toml` starts from the shared Oxford
dictionary. Its builder refreshes the untracked `.typos-oxendict-base.toml`
cache and metadata only when the shared dictionary is newer, so the last
fetched base remains usable in a network-restricted checkout.

Keep repository-specific identifiers and deliberate quotations in
`typos.local.toml`. Run `make spelling-config-write` to regenerate the tracked
configuration and `make spelling-config` to verify it. Never edit generated
entries by hand.

## Architecture Decision Records

- [ADR 0002: Explicit ps-module-name for PowerShell sidecars](adr/0002-explicit-ps-module-name.md)

## Python Coverage Venv Architecture

### Motivation

Earlier revisions of `run_python.py` invoked slipcover and coverage.py via
`uv run --with slipcover --with pytest --with coverage python ...`, which
re-resolved and reinstalled tooling on every invocation and could not cache the
interpreter reference within the same process.

### Lifecycle

`run_python.py` manages a dedicated throwaway virtual environment at
`.venv-coverage` in the working directory.

| Step | Function                    | Description                               |
| ---- | --------------------------- | ----------------------------------------- |
| 1    | `_find_coverage_python()`   | Locate the Python executable.             |
| 2    | `_remove_coverage_venv()`   | Remove the venv or placeholder path.      |
| 3    | `_recreate_coverage_venv()` | Recreate the venv.                        |
| 4    | `_ensure_coverage_venv()`   | Sync project deps and install tooling.    |
| 5    | `_coverage_python_cmd()`    | Return the cached `plumbum.BoundCommand`. |

`_find_coverage_python()` returns `None` when `.venv-coverage` is absent, is a
symlink, is a non-directory, or lacks a Python executable.
`_remove_coverage_venv()` uses `shutil.rmtree` for directories and
`Path.unlink` for files or symlinks. `_recreate_coverage_venv()` raises
`RuntimeError` if the executable is still absent after creation.
`_ensure_coverage_venv()` runs `uv sync --inexact --python <venv_python>` and
`uv pip install --python <venv_python> slipcover>=1.0.18 pytest pytest-xdist coverage`.
The `slipcover>=1.0.18` floor ensures the xdist plugin is present so
`pytest -n <workers>` runs merge per-worker coverage transparently; the
constraint also forces uv to upgrade any older slipcover installed earlier by
`uv sync`. `<venv_python>` is the absolute path inside `.venv-coverage`; it is
not resolved through symlinks before being passed to uv.
`_coverage_python_cmd()` uses `@lru_cache(maxsize=1)` and returns the cached
command for `<venv_python>` thereafter.

### Public API

<!-- markdownlint-disable MD013 -->
| Symbol                 | Signature                                                                     | Role                                                                        |
| ---------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `coverage_cmd_for_fmt` | `(fmt, out, workers="")`                                                      | Build a slipcover command, optionally with `-n <workers>` for pytest-xdist. |
| `tmp_coveragepy_xml`   | `(out)`                                                                       | Generate temporary Cobertura XML.                                           |
| `main`                 | `(output_path, lang, fmt, github_output, baseline_file, pytest_workers=None)` | Run.                                                                        |
<!-- markdownlint-enable MD013 -->

`coverage_cmd_for_fmt` returns a `BoundCommand` for the requested format. When
`workers` is non-empty, it appends `-n <workers>` so pytest-xdist parallelizes
the run; an empty string preserves the historical serial pytest invocation.
`tmp_coveragepy_xml` yields a temporary XML path and removes it on exit. `main`
resolves `pytest_workers` from the CLI option, falling back to the
`INPUT_PYTEST_WORKERS` environment variable and finally to `"auto"`. Accepted
values are `"auto"`, `"logical"`, a positive integer string, or `""` to disable
parallelism — `"0"` is rejected so that `""` stays the single canonical disable
mechanism. `main` then runs slipcover, parses coverage, and writes
`GITHUB_OUTPUT`.

### Concurrency Model

`run_python.py` runs as a single-threaded GitHub Actions step. The
`@lru_cache(maxsize=1)` on `_coverage_python_cmd()` therefore requires no
explicit synchronization; the cache is safe for the lifetime of the process.

### Broken-Venv Recovery

If `.venv-coverage` is present but its Python executable is absent (or a
non-directory placeholder occupies its path), `_recreate_coverage_venv()`
removes the directory and recreates it from scratch. The case is detected by
`_find_coverage_python()` returning `None` when the directory already exists.

### POSIX and Windows Layouts

`_find_coverage_python()` checks three candidate paths in order:

- `COVERAGE_VENV/bin/python` (POSIX)
- `COVERAGE_VENV/Scripts/python.exe` (Windows)
- `COVERAGE_VENV/Scripts/python` (Windows without extension)

On POSIX, `bin/python` is commonly a symlink to the base interpreter, for
example `/usr/bin/python3.12`. `_find_coverage_python()` deliberately returns
`Path.absolute()` rather than `Path.resolve()` so the action passes
`.venv-coverage/bin/python` to uv. Resolving that symlink would make
`uv pip install --python` target the externally managed system interpreter
instead of the throwaway coverage venv.

The helper logs each candidate at DEBUG level with the candidate path, absolute
path, resolved target, file status, and symlink status.
`_ensure_coverage_venv()` logs the candidate set and the exact Python path
passed to `uv sync` and `uv pip install` at INFO level so CI logs can identify
whether uv targeted the venv path or the base interpreter.

## Makefile Tool Resolution

The `Makefile` resolves optional local tool installations before falling back
to bare names on `PATH`.

| Variable           | Default resolution order                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `UV`               | `~/.local/bin/uv` if present, otherwise `uv`                                                                 |
| `ACT`              | `~/go/bin/act` if present, then `~/.local/bin/act` if present, otherwise `act`                               |
| `ACTION_VALIDATOR` | `~/.bun/bin/action-validator` if present, then `~/.cargo/bin/action-validator`, otherwise `action-validator` |
| `MDLINT`           | `~/.bun/bin/markdownlint-cli2` if present, otherwise `markdownlint-cli2`                                     |

Override example:

```bash
make lint UV=uv
make test ACT=/usr/local/bin/act
```

## `setup-uv` Pinning

Actions and workflows in this repository consume `astral-sh/setup-uv` by full
commit SHA rather than by mutable version tags. This follows the repository
security rule for third-party actions: callers should execute a reviewed Git
object, not whatever a tag happens to resolve to later.

Keep all `setup-uv` references on the same SHA unless there is a deliberate
compatibility reason to split them. A pin update is repository-wide
maintenance: search for `astral-sh/setup-uv@`, update every matching action or
workflow reference together, and run the normal action test gates before review.

When changing the pin, include the target SHA in the change description and
verify affected act workflow tests where the action runs under `nektos/act`. If
act cannot execute the real `setup-uv` path on the local runner, document the
reason and keep the unit or manifest tests that assert the pinned reference in
sync with the new SHA.

## `upload-codescene-coverage` check-mode contract

The `gate-applicability` step runs only when `inputs.mode` is `check`. It
compares the non-empty `github.base_ref` with
`github.event.repository.default_branch`. When they differ, it writes
`skip=true` to `GITHUB_OUTPUT` and emits a warning explaining that the base is
not an analysed branch. An empty base does not trigger this skip, which keeps
the applicability check usable outside a pull-request event.

The applicability output is the boundary for the rest of the action. Every
following step — coverage-path resolution, installer download, GitHub
artefact upload, cache and CLI installation, PATH setup, and the upload/check
commands — must require
`steps.gate-applicability.outputs.skip != 'true'`. Do not add a check-mode
step outside that guard unless it is deliberately meant to run for skipped
pull requests.

The check command is an observable diagnostic contract. After validating the
CLI, coverage file, and LCOV suffix, run
`cs-coverage check --verbose --coverage-files "$file"` directly so its native
standard-output and standard-error streams remain intact. Put the invocation
in an `if` condition; in the failure branch, capture `$?` as the first
command, add the uploaded-base explanation when the status is `2`, then
`exit "$status"`. This preserves every CLI failure status rather than
masking it with diagnostic handling. The behavioural contract is covered by
the [check-mode tests](../.github/actions/upload-codescene-coverage/tests/test_check_mode.py).

## `setup-rust` cargo-binstall Pinning

The `setup-rust` action pins `cargo-binstall` by downloading
`install-from-binstall-release.sh` from a tagged cargo-binstall release and
checking the installer script against a fixed SHA-256 digest. Treat the version
tag and checksum as a pair: update both in the same change and keep the
checksum tied to the installer script at that tag.

`BINSTALL_VERSION` must be exported in the install step. The installer script
is executed by a child `bash` process and reads `BINSTALL_VERSION` from its
environment to decide whether to download from `releases/download/<version>/`
or from `releases/latest/download/`. If the value is only a shell variable, the
child process sees it as empty and silently uses `latest`, defeating the pin.

After the installer runs, the action verifies the installed binary with
`cargo-binstall -V` and fails with the actual version in the log if it does not
match the pinned release. Keep that runtime check in sync with
`BINSTALL_VERSION` whenever the pin changes.

## `generate-coverage` cargo-binstall Pinning

`generate-coverage` provisions its own `cargo-binstall` in the "Ensure
cargo-binstall" step before installing `cargo-llvm-cov` or `cargo-nextest`,
both of which shell out to `cargo binstall`. It follows the same pinning
discipline as `setup-rust`: `BINSTALL_VERSION` and the installer-script
`BINSTALL_SHA256` are a pair and must be updated together.

The step is idempotent and verifies the version on both paths:

- **Fast path** — if `cargo-binstall` is already on `PATH`, its `-V` output is
  matched against the pinned version. On a match the step reuses the binary and
  exits without any network access; on a mismatch it logs the discrepancy and
  falls through to a pinned reinstall.
- **Install path** — the checksum-pinned installer script is downloaded and its
  SHA-256 verified before execution, and the freshly installed binary's version
  is re-checked so a wrong installed version fails the step.

Both paths are exercised by behavioural tests in
`.github/actions/generate-coverage/tests/test_scripts.py`, which execute the
extracted step body against fake `cargo-binstall` binaries and installers
rather than asserting on the step's source text.

## `generate-coverage` `cargo-nextest` Installation

`install_cargo_nextest.py` resolves expected checksums using `_platform_key()`.
On Linux, `_platform_key()` calls `_is_musl()` to choose between
`linux-<arch>-gnu` and `linux-<arch>-musl` keys before looking up
`CARGO_NEXTEST_SHA256`.

`_is_musl()` wraps libc probing in one place via injectable `ctypes.CDLL`
/symbol lookup and surfaces probe failures through the normal error path, so
orchestrating code consumes a concrete `typer.Exit` from
`_expected_sha_for_platform()` and keeps loader details local to the installer.

### CARGO_HOME resolution and PATH handling

The install step derives the active Cargo bin directory at runtime:

```bash
cargo_home_bin="${CARGO_HOME:-$HOME/.cargo}/bin"
```

This respects any custom `CARGO_HOME` set by the caller. The resolved path is
used for three purposes:

1. **GITHUB_PATH** – when `GITHUB_PATH` is set, the resolved bin directory is
   appended so that subsequent workflow steps see the binary on their `PATH`.
2. **Current-step PATH** – the bin directory is prepended to the *current*
   shell's `PATH` (guarded by a `case ":$PATH:"` check to avoid duplication) so
   that in-step commands can also find the binary.
3. **Absolute-path verification** – `cargo-binstall` is invoked via its
   resolved absolute path (`"$cargo_binstall"`) rather than as an unqualified
   command, ensuring that verification succeeds even when the bin directory has
   not yet been propagated to the shell's `PATH` by other means.

Keep `cargo_home_bin` resolution and the `BINSTALL_VERSION` pin in sync: both
must reflect the same intended installation location and version whenever the
pin is updated.

## `generate-coverage` nextest checksum strategy

`generate-coverage` delegates Rust nextest installation to
`.github/actions/generate-coverage/scripts/install_cargo_nextest.py`.

The helper validates a pinned `cargo-nextest` version and picks the expected
SHA-256 using a platform key. Linux x86_64 is split into two keys:

- `linux-x86_64-gnu` for the `-x86_64-unknown-linux-gnu` archive.
- `linux-x86_64-musl` for the `-x86_64-unknown-linux-musl` archive.

This distinction is intentional because the upstream artefacts are built
against different libc ABIs, and validating against the wrong digest can block
installs even when the same version number is used.

## `stage-release-artefacts` Action Architecture

### Staging Pipeline

The `stage-release-artefacts` action is implemented by
`.github/actions/stage-release-artefacts/scripts/stage.py`, which loads a TOML
configuration and delegates staging to `stage_common.pipeline.stage_artefacts`.
The pipeline renders configured source and destination templates, copies each
matched artefact into the staging directory, writes checksum sidecar files, and
returns a `StageResult`. The CLI owns infrastructure concerns: it reads
`GITHUB_WORKSPACE` and `GITHUB_OUTPUT`, emits GitHub Actions warning
annotations for skipped optional artefacts, and writes workflow outputs.

`stage_common.config.load_config` requires callers to pass the workspace
explicitly via `workspace=...`; it no longer reads `GITHUB_WORKSPACE` itself.
This keeps configuration loading independent from the process environment. The
CLI remains responsible for resolving `GITHUB_WORKSPACE` with
`require_env_path` and injecting that path into `load_config` before staging
(issue `#266`).

`_collect_artefacts` owns the collection phase. It iterates over the configured
artefacts, records the staged paths, builds the map of named outputs, and
collects checksums keyed by staged relative path. `stage_artefacts` validates
reserved output names, resolves optional PowerShell sidecar metadata, logs
start and completion records with counts and elapsed time, and returns a
`StageResult` without writing `GITHUB_OUTPUT`.

### Output Data

`stage_common.output.StagingOutputData` is the parameter object passed to
`prepare_output_data`. It keeps the output formatter explicit without growing
the function argument list. The object contains the staging directory, staged
paths, named output paths, checksum map, and the optional `powershell_help_dir`.
`prepare_output_data` serializes path values with `Path.as_posix()` so
workflow outputs use forward slashes consistently across platforms.

The CLI converts `StageResult` into `StagingOutputData` immediately before
calling `write_github_output`. Tests that assert output-file structure redact
absolute staging paths before snapshot comparison so path-sensitive output
remains deterministic across runners.

### PowerShell Help Directory

`_resolve_powershell_help_dir` only exports a PowerShell module directory when
`ps-module-name` names a single direct child of the staging directory and at
least one staged file exists below that directory. Empty names, `"."`, `".."`
and names containing path separators return `None`. The resolved module
directory must also have `staging_dir.resolve()` as its parent, which prevents
parent-directory traversal and nested module paths from being exported. The
pipeline logs the reason a PowerShell directory was not exported, including
empty input, invalid module names, and missing staged files below the module
directory.

The action metadata and README document the public `ps-module-name` input and
`powershell_help_dir` output. Keep that public contract in sync with the
internal rules above whenever changing PowerShell sidecar staging.

### Observability

Each staging run has a correlation ID (`corr_id`) generated by `stage.py` for
CLI execution or by `stage_common.pipeline.stage_artefacts` for direct callers.
Pipeline INFO, WARNING, and DEBUG records include that `corr_id` so start,
per-artefact, PowerShell-resolution and failure records can be grouped in CI
logs.

The staging pipeline emits INFO records when staging starts and completes. The
start record includes the target, artefact count, staging directory and
`ps_module_name`; the completion record includes staged, skipped, checksum and
output counts, elapsed time in `elapsed_ms`, and the resolved
`powershell_help_dir` value. PowerShell resolution emits INFO records for empty
names, rejected module names, missing files below the requested module
directory, and successful resolution.

Skipped optional artefacts are surfaced as WARNING-level GitHub annotations by
`stage.py`, keeping optional sidecar misses visible in workflow logs without
turning them into failures.

DEBUG records cover each resolved staged artefact, including source path,
destination path and checksum digest, plus the PowerShell module-directory
existence checks. `stage.py` logs exceptions with the same `corr_id` before
emitting the GitHub Actions `::error` annotation. Use pytest's log options to
enable DEBUG output during local investigation or CI repro jobs:

```bash
pytest -o log_cli=true --log-level=DEBUG
```

## Workflow Test Harness (`tests/workflows/conftest.py`)

### Runtime Probing

The workflow test harness determines whether `act` and a compatible container
runtime are available before running tests. The probe result is represented by:

<!-- markdownlint-disable MD013 -->
| Symbol                    | Type                             | Role                                                                                       |
| ------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------ |
| `ActRuntimeStatus`        | frozen dataclass                 | Holds `available: bool`, `reason: str`, `env: dict[str, str]`.                             |
| `_probe_act_runtime`      | `(environ?) -> ActRuntimeStatus` | Execute a fresh probe against the given environment mapping (defaults to `os.environ`).    |
| `_get_act_runtime_status` | `() -> ActRuntimeStatus`         | Return the cached probe result, performing the probe on first call.                        |
| `_act_command`            | `(environ?) -> str`              | Return the act executable path from the `ACT` environment variable, defaulting to `"act"`. |
<!-- markdownlint-enable MD013 -->

`_get_act_runtime_status` is decorated with `@functools.cache` so the probe
runs at most once per process. Tests that need to observe a different runtime
environment must call `_probe_act_runtime(environ)` directly with an explicit
mapping, bypassing the cache.

`ActRuntimeStatus.env` carries any additional environment variables that must
be injected into the `act` subprocess - currently used to forward `DOCKER_HOST`
when a healthy Podman socket is discovered automatically.

### Skip Markers

<!-- markdownlint-disable MD013 -->
| Marker                       | Condition                                                    |
| ---------------------------- | ------------------------------------------------------------ |
| `skip_unless_act`            | Skip when `_get_act_runtime_status().available` is `False`.  |
| `skip_unless_workflow_tests` | Skip when `ACT_WORKFLOW_TESTS` is not set to a truthy value. |
<!-- markdownlint-enable MD013 -->

## Mutation-Testing Reusable Workflows

Two reusable workflows provide scheduled, informational mutation testing for
callers: `.github/workflows/mutation-cargo.yml` (Rust,
[cargo-mutants](https://mutants.rs/)) and
`.github/workflows/mutation-mutmut.yml` (Python,
[mutmut](https://mutmut.readthedocs.io/)). Caller-facing usage lives in
[mutation-cargo-workflow.md](mutation-cargo-workflow.md) and
[mutation-mutmut-workflow.md](mutation-mutmut-workflow.md); design history,
empirical findings, and decisions live in the
[execplan](execplans/add-mutation-testing-workflows.md).

Internals for maintainers:

- All non-trivial logic lives in `workflow_scripts/` helper scripts
  (Cyclopts, `INPUT_*` environment configuration, plumbum):
  `mutation_detect_changes.py` (change-detection guard and shard-matrix
  construction, shared by both workflows), `mutation_run_cargo.py`
  (cargo-mutants invocation and the informative exit-code contract: 0/2/3
  succeed, everything else fails with the tool's code),
  `mutation_summarize_cargo.py` (merges shard `outcomes.json` artefacts and
  renders the job summary), and `mutation_run_mutmut.py` (module-glob scoping,
  results parsing, and summary in one pass — mutmut has no shard support).
- Tool version pins default in the workflows because both report
  formats are unstable; a version bump must be paired with a parser check
  (`outcomes.json` fields for cargo-mutants; the `mutmut results --all true`
  line format for mutmut).
- Unit tests fake the `cargo`/`uv` boundary with POSIX shell shims on
  `PATH`; those tests are skipped on Windows (the workflows only run on
  `ubuntu-latest`). Property-based tests in
  `workflow_scripts/tests/test_mutation_properties.py` cover the bucketing,
  translation, and parsing invariants.
- The act integration tests
  (`tests/workflows/test_mutation_workflows.py`) exercise the change-detection
  skip path end-to-end; the mutation-run path cannot be act-tested because stub
  binaries cannot be injected onto a `workflow_call` job's `PATH`.
- Workflow-source resolution lives in the
  `.github/actions/resolve-workflow-source` composite action (see its README).
  Because that action performs the SHA resolution itself, the reusable
  workflows reference it by a hardcoded full-commit pin that must be bumped
  manually whenever the action changes — it is the one reference that cannot
  participate in the caller's version lockstep. Its act short-circuit and OIDC
  fail-fast branches are exercised by
  `tests/workflows/test_resolve_workflow_source.py`; the OIDC happy path is
  validated by every real run of the consuming workflows.

## Running the Test Suite

```bash
make test          # full suite
make check-fmt     # Ruff formatting check
make typecheck     # mypy
make lint          # Ruff lint + action-validator + markdownlint
```

## `install-nixie` Action Maintenance

The composite action boundary is
`.github/actions/install-nixie/action.yml`. It requires `cargo` and `uv` on
`PATH` and exposes three pins: `nixie-version` defaults to `1.1.0`,
`merman-version` defaults to `0.7.0`, and `python-version` defaults to `3.14`.
Keep those public inputs and their defaults synchronized with the action README
and users' guide.

The Merman policy is cargo-binstall first: use locked `cargo binstall` when its
availability probe succeeds, then fall back to locked `cargo install` only when
cargo-binstall is unavailable. The install step uses `set -euo pipefail` as its
failure boundary. Missing prerequisites or a failed Merman or Nixie installer
must stop the step before PATH export.

After both installers succeed, `uv tool dir --bin` supplies the directory
appended to `GITHUB_PATH`. This makes `nixie` available to later workflow steps;
do not run the lookup or write `GITHUB_PATH` after an installation failure.

`.github/actions/install-nixie/tests/test_action.py` is the behavioural test
boundary. Changes to this action must retain coverage for both successful
Merman selection paths, missing `cargo` and `uv`, failures from cargo-binstall,
the cargo-install fallback, and `uv tool install`, plus bounded property-based
coverage of shell-safe versions across both installer-selection states. Failure
tests must prove later installers and PATH export were not reached.

## `rust-build-release` Action Architecture

### Man-Page Path Strategy

The `rust-build-release` composite action stages compiled Rust man pages for
release packaging. Man pages are expected at a deterministic location:

```text
target/generated-man/<TARGET>/<PROFILE>/<bin>.1
```

This path is written by the consuming project's `build.rs` script. The build
script derives `target/` from `CARGO_TARGET_DIR` when that variable is set (as
it is when `cross` mounts the workspace inside a Docker container), and
otherwise walks five ancestor levels of Cargo's hash-dependent `OUT_DIR`.

If the stable path is absent the staging step falls back to scanning
`target/<triple>/release/build/*/out/` - the legacy Cargo build-output location

- and emits a `::warning::` annotation. Zero or multiple matches in the legacy
location are fatal errors.

When `skip-man-page-discovery` is set to `'true'`, the staging step bypasses
all man-page discovery and installation; no `man-path` output is written to
`GITHUB_OUTPUT`. When it is `'false'` (the default), the existing stable-path
and legacy fallback behaviour applies. Callers that generate man pages in a
post-`cross build` step, for example via `cargo-orthohelp`, must set this input
to `'true'` and handle staging themselves.

### Fingerprint Invalidation

`build.rs` emits `cargo:rerun-if-env-changed=CARGO_TARGET_DIR` so that a change
in the Docker bind-mount target directory (e.g. between cached and uncached CI
runs) forces the build script to rerun and regenerate the man page at the
correct stable path.

### Build Observability

`build.rs` emits a `cargo:warning=writing man page to ...` diagnostic so that
the chosen stable path is visible in the `cargo build` log. The staging step
emits
`::warning::stable man-page path ... was absent; using legacy fallback ...`
when the fallback activates.

### Testing

Shell-script behaviour of the staging step is exercised by
`.github/actions/rust-build-release/tests/test_stage_script_behaviour.py`,
which extracts the `stage-artefacts` run block from `action.yml`, parametrizes
it, and runs it under bash. Five scenarios are covered: stable path present,
legacy fallback, missing man page (error), multiple legacy matches (error), and
skip mode (no man-page staging, binary only). Tests are automatically skipped
on Windows.

### RUSTFLAGS Export

Both `setup-rust` and `rust-build-release` expose a `rustflags` input, but
they wire it differently. `setup-rust` forwards the input straight through to
each of its three `actions-rust-lang/setup-rust-toolchain` invocations, so
what happens to an inherited `RUSTFLAGS` is that nested action's decision.
`rust-build-release` instead exports the value itself, in an "Export caller
RUSTFLAGS" step that runs *before* its own pinned nested `setup-rust` step
(see `.github/actions/rust-build-release/action.yml`), so that step's
`setup-rust-toolchain` — which only applies its `-D warnings` default when
`RUSTFLAGS` is unset — defers to the caller's value. The design rationale for
this split lives in section 3.1.3, "Caller-Controlled `RUSTFLAGS`", of the
[Rust Build and Release Pipeline design](rust-build-release-pipeline.md); the
caller-facing usage is in the [users' guide](users-guide.md). This section
covers the implementation detail a maintainer needs to change the export step
safely.

#### Precedence guard

The export step is skipped entirely by `if: inputs.rustflags != ''`, but even
when it runs it must not clobber a `RUSTFLAGS` the caller already exported.
It guards with `[[ ${RUSTFLAGS+x} ]]`, which is true whenever `RUSTFLAGS` is
set, including to the empty string, so an inherited value — empty or not —
always wins over the input. `setup-rust` has no equivalent guard; forwarding
the empty string to it leaves `RUSTFLAGS` alone only because
`setup-rust-toolchain` treats an empty forwarded value as "unset".

#### Bash 3.2 compatibility

`[[ ${RUSTFLAGS+x} ]]` is used rather than the more idiomatic
`[[ -v RUSTFLAGS ]]` because `-v` needs Bash 4.2 and macOS runners ship Bash
3.2, which cannot parse that conditional primary. Both forms treat an
inherited empty value as set. Keep this constraint in mind for any future
edit to this or similar shell fragments in the two actions: parameter
expansion of the `${NAME+x}` form, not `-v`, is the portable way to test "is
this variable set".

#### `GITHUB_ENV` heredoc safety

The step writes `RUSTFLAGS` to `GITHUB_ENV` as a heredoc rather than a plain
assignment because the value may contain newlines. The delimiter is derived
from 16 random bytes (`od -An -N16 -tx1 /dev/urandom`) and checked against
the value with `grep -qxF` before use. If a value contained the delimiter on
a line of its own, that line would close the heredoc block early, and
whatever followed would be read back by the runner as further
environment-file commands — an injection route, not just a formatting bug.
The step retries with a fresh candidate up to three times and fails the step,
rather than writing an unsafe delimiter, if all three collide.

#### RUSTFLAGS export observability

The step logs three kinds of event, all to `stderr`:

- deferral to an inherited value ("RUSTFLAGS already set; leaving the
  inherited value in place");
- each delimiter-collision attempt, numbered out of the fixed retry budget
  ("RUSTFLAGS delimiter attempt `N` of 3 collided with the value; retrying");
- the successful export, also numbered ("RUSTFLAGS exported from the
  rustflags input on attempt `N` of 3").

It deliberately never logs the `RUSTFLAGS` value itself or a colliding
delimiter candidate: a candidate only collides because the value contains it
as a substring, so echoing the candidate would leak a line of the caller's
`RUSTFLAGS` into the CI log.

#### RUSTFLAGS export testing

`.github/actions/rust-build-release/tests/test_rustflags_export.py` extracts
and runs the export step's shell fragment under bash. It covers the
precedence guard (including an inherited empty value), the heredoc
round-trip for adversarial payloads via Hypothesis properties, the
delimiter-collision retry and give-up paths (using a stubbed `od` to make a
collision reachable), and that neither the value nor a colliding candidate
reaches the log.
`.github/actions/rust-build-release/tests/test_manifest_input_step.py` checks
the manifest's declared shape instead: the `rustflags` input's empty default,
the export step's `if` condition and `RBR_RUSTFLAGS` wiring, the
`${RUSTFLAGS+x}` guard's presence in the run script, and that the export step
precedes toolchain setup.
