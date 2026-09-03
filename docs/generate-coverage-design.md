# Generate Coverage — Design Notes

This document captures the architectural choices for the `generate-coverage`
action and the evolution of its supporting scripts.

## Design Decisions

- *2025-11-06* — Coverage artefact names now include the runner operating
  system and architecture, with an optional caller-provided suffix. The
  metadata is computed by `set_outputs.py`, which detects the platform via a
  `plumbum`-driven Python subprocess and exposes the composed name to the
  workflow. The script migrated to `cyclopts` for CLI parsing so additional
  inputs can be mapped declaratively from the GitHub Actions environment.
- *2026-04-16* — Rust coverage runs now force LLVM via subprocess environment
  overrides instead of outer `cargo --config ...` flags when a repository
  configures the Cranelift backend. This keeps the action compatible with
  `cargo-llvm-cov`, which spawns nested Cargo commands that inherit environment
  variables but do not inherit the wrapper process's ad hoc `--config` flags.
- *2026-04-27* — Python coverage runs now execute inside an isolated,
  short-lived virtual environment (`.venv-coverage`) rather than relying on
  `uv run --with` or the system interpreter. `_ensure_coverage_venv()` creates
  or repairs the venv on first use, syncs the project dependencies into it via
  `uv sync --inexact --python`, installs tooling (`slipcover`, `pytest`,
  `coverage`) via `uv pip install --python`, and `_coverage_python_cmd()`
  caches the resulting interpreter command for the lifetime of the process.
- *2026-04-30* — Python coverage venv discovery now preserves the absolute
  venv interpreter path instead of resolving it through symlinks. On Linux,
  `.venv-coverage/bin/python` can point at `/usr/bin/python3.12`; passing the
  resolved target to `uv pip install --python` makes uv treat `/usr` as the
  install environment and trips externally-managed-interpreter protections. The
  action must pass the venv path itself to uv, and it logs the candidate paths,
  resolved targets, and selected uv command interpreter for diagnosis.
- *2026-06-16* — `install_cargo_nextest.py` now distinguishes GNU and musl
  Linux runtimes when selecting checksum keys. `_platform_key` delegates to
  `_is_musl`, which probes libc through `ctypes.CDLL` and logs the detected
  libc family so failures can be diagnosed from output logs.
- *2026-06-04* — Python coverage runs adopt `pytest-xdist` by default. A new
  `pytest-workers` input (default `auto`) is forwarded as `-n <workers>` to
  slipcover's pytest invocation, and `pytest-xdist` is installed alongside the
  existing coverage tooling. The slipcover tooling spec is pinned to
  `slipcover>=1.0.18` — the first release whose xdist plugin merges per-worker
  coverage transparently — so an older slipcover already present in the
  project's environment is upgraded by uv rather than left in place. The
  validator rejects `"0"` to keep `""` the single canonical way to disable
  parallelism: pytest-xdist treats `-n 0` as a no-op but the action's public
  contract documents only the empty value. Known caveat: slipcover 1.0.18's
  xdist plugin does not propagate `--omit` to worker processes, so projects
  whose tests live inside the source package see their reported line-rate drop
  until they relocate tests or set `pytest-workers: ""`. This is documented in
  the action's README.
- *2026-07-04* — `generate-coverage` now provisions its own pinned
  `cargo-binstall` before installing Rust coverage tooling, mirroring the
  `setup-rust` approach. The "Ensure cargo-binstall" step verifies any existing
  `cargo-binstall` against the pinned version (`v1.19.1`) and reuses it only on
  a match; on a mismatch, or when the binary is absent, it downloads the
  SHA-256-pinned installer script and verifies the freshly installed binary's
  version before continuing. This stops `cargo-llvm-cov` and `cargo-nextest`
  installation — both of which shell out to `cargo binstall` — from silently
  relying on an unpinned or stale binary already present on the runner. Both
  the fast (reuse) and install paths are covered by behavioural tests that
  execute the extracted step body against fake binaries and installers.
- *2026-09-03* — The ratchet baseline cache moved from the full `actions/cache`
  action to the `actions/cache/restore` and `actions/cache/save` sub-actions at
  one pinned revision. The full action registers a post-job save of its own, so
  pairing it with an explicit save step gave the run-id-suffixed key two
  writers and every run lost the reservation. The lifecycle invariant is
  ordered rather than counted: the earlier step reads and does not write, the
  later writes and does not read. A dedicated always-run step reports bounded
  outcomes for both halves, with `skipped` distinguished from `disabled` so an
  earlier failure that skips the restore is not reported as a switched-off
  cache.
- *2026-09-03* — `all-features`, `all-targets`, and `doctests` inputs let one
  coverage job be a repository's whole test run instead of a second execution
  beside a separate test job. All three default to off. Feature selection is
  split along command/query lines: `feature_selection_args` is a pure builder,
  `feature_selection_diagnostics` a pure query returning the error and warning
  a selection deserves, and `check_feature_selection` the only place that
  writes output or exits, called once from `main`. That keeps the precedence
  rule in one place: `all_features` supersedes `with_default`, so
  `--all-features --no-default-features` can never be emitted, and it is
  rejected alongside a non-empty feature list rather than silently widening
  what the caller named. Doc tests are a separate Cargo target kind that
  `--all-targets` does not reach and `--doc` cannot be combined with, so they
  run afterwards as a plain uninstrumented `cargo test --doc --workspace`
  carrying the same feature selection. A caller's `RUSTFLAGS` reaches every
  Cargo invocation, because the spawned environment starts from a copy of
  `os.environ` and neither the coverage overrides nor the unset list names it.

## Rust Coverage Environment Overrides

### Problem Statement

Some Rust repositories set:

```toml
[profile.dev]
codegen-backend = "cranelift"
```

to speed up normal local builds. That is a valid repository-level choice, but
it breaks source-based coverage because `-Cinstrument-coverage` only works with
LLVM. Earlier versions of `run_rust.py` tried to work around this by launching
the outer coverage command with:

```text
cargo --config 'profile.dev.codegen-backend="llvm"' \
      --config 'profile.test.codegen-backend="llvm"' \
      llvm-cov ...
```

That looked correct at first glance, but it only affected the wrapper Cargo
process. `cargo-llvm-cov` then spawned nested `cargo test` or `cargo nextest`
processes, and those child processes still read the repository's
`.cargo/config.toml` with `codegen-backend = "cranelift"`. The result was the
same failure the action was supposed to prevent:

```text
error: `-Cinstrument-coverage` is LLVM specific and not supported by Cranelift
```

### Current Design

`run_rust.py` now splits the behaviour into two explicit pieces:

1. `get_cargo_coverage_env(manifest_path)` decides whether coverage-specific
   Cargo environment overrides are needed.
2. `_run_cargo(args, env_overrides=...)` merges those overrides into the
   subprocess environment before invoking Cargo.

When the target repository does not use Cranelift, `get_cargo_coverage_env()`
returns an empty mapping and the action behaves as before.

When Cranelift is detected, the helper returns:

```text
CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm
CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm
```

These variables are passed to the `cargo llvm-cov` subprocess and therefore
propagate into the nested Cargo commands that perform the actual compilation.

### Why Environment Variables Instead of `--config`

The environment-variable approach is intentional rather than incidental:

- Cargo child processes inherit environment variables by default.
- `cargo-llvm-cov` launches nested Cargo commands internally.
- Those nested commands do not inherit wrapper-only `--config` CLI arguments.
- Coverage therefore needs a transport mechanism that survives process
  boundaries.

Using `CARGO_PROFILE_DEV_CODEGEN_BACKEND` and
`CARGO_PROFILE_TEST_CODEGEN_BACKEND` keeps the override tightly scoped to the
coverage subprocess. The repository's checked-in configuration stays unchanged,
and non-coverage flows continue to use Cranelift if the repository asked for it.

### Where the Overrides Apply

The overrides are applied in both Rust coverage entry points:

- the main `cargo llvm-cov` run
- the optional cucumber.rs follow-up run when `with-cucumber-rs` is enabled

Both paths call the same `get_cargo_coverage_env()` helper so the logic does
not drift between the primary and follow-up coverage invocations.

### Behaviour of `env_overrides`

`_run_cargo(..., env_overrides=..., env_unsets=...)` accepts two optional
inputs:

- `env_overrides=None` means "do not add replacement values"
- `env_unsets=()` means "do not remove any inherited keys"
- when `env_overrides` is a mapping, apply these overrides
- when `env_unsets` is an iterable of key names, remove these inherited
  variables before applying overrides

The helper still starts from `os.environ` so it preserves PATH, toolchain
configuration, and the rest of the GitHub Actions runtime context, but it now
explicitly removes inherited `CARGO_PROFILE_DEV_CODEGEN_BACKEND` and
`CARGO_PROFILE_TEST_CODEGEN_BACKEND` before applying coverage overrides. That
prevents a caller or runner host from leaking an unrelated Cranelift preference
into coverage runs.

### Cranelift Detection Strategy

Cranelift detection is intentionally lightweight, but it checks two sources
before deciding coverage needs LLVM overrides:

- `_uses_cranelift_backend(manifest_path)` walks upward from the selected Cargo
  manifest directory and scans `.cargo/config.toml` plus `.cargo/config`.
- `_manifest_uses_cranelift_backend(manifest_path)` reads the selected
  `Cargo.toml`.
- The selected manifest is scanned for profile sections containing
  `codegen-backend = "cranelift"` or the single-quoted equivalent.

The action therefore catches repository-level Cargo config overrides and
per-manifest profile settings using two lightweight text scans:
`.cargo/config*` detection stays regex-based, while
`_manifest_uses_cranelift_backend()` walks the selected `Cargo.toml` line by
line and checks only `[profile]` sections.

### Known Limitations

The current detection path is intentionally simple and has limits developers
should understand:

- `.cargo/config*` detection is text/regex-based rather than
  TOML-structure-aware, so any matching `codegen-backend = "cranelift"`
  assignment in those files triggers the override. Manifest profile detection
  is likewise text-based: `_manifest_uses_cranelift_backend()` scans the
  selected `Cargo.toml` for `[profile]` and `[profile.*]` sections before
  matching `codegen-backend = "cranelift"` assignments inside them.
- It only inspects `.cargo/config.toml`, `.cargo/config`, and the selected
  `Cargo.toml`. It does not model configuration injected via CLI `--config`,
  environment-backed Cargo config beyond the explicit dev-profile unset, or
  other runtime indirection.
- It always applies the `dev` and `test` profile overrides once Cranelift is
  detected. The action currently does not try to mirror per-profile granularity
  from the repository config.
- Files that cannot be read as UTF-8 are ignored rather than failing the run,
  because the action prefers a conservative fallback over blocking coverage for
  an unrelated config parse issue.
- When `cargo-manifest` points to a workspace member,
  `_manifest_uses_cranelift_backend` only inspects that member's `Cargo.toml`.
  Profile overrides in the workspace root `Cargo.toml` are not scanned via this
  path. Use `.cargo/config.toml` at the workspace root to ensure detection
  works regardless of which member manifest is selected.

If the repository ever needs finer-grained handling, the next step would be a
real TOML parser plus table-aware resolution. The current design intentionally
stops short of that complexity.

## Shared Script Helpers (`common.py`)

### Purpose

`common.py` is a shared utility module imported by `run_rust.py`,
`run_python.py`, and `merge_cobertura.py`. It centralizes environment-variable
handling so error messages and exit codes are consistent across all scripts.

### Public API

<!-- markdownlint-disable MD013 -->
| Symbol          | Signature                               | Role                                                                              |
| --------------- | --------------------------------------- | --------------------------------------------------------------------------------- |
| `_required_env` | `(name: str) -> str`                    | Return the non-empty value of a required env var or exit with code 2.             |
| `_env_bool`     | `(name: str, *, default: bool) -> bool` | Parse a boolean env var; raise `typer.Exit(2)` for unrecognized non-empty values. |
<!-- markdownlint-enable MD013 -->

`_required_env` trims whitespace before testing emptiness so a variable set to
only spaces is treated as absent.

`_env_bool` accepts the following case-insensitive truthy values: `1`, `true`,
`yes`, `on`; and the following falsy values: `0`, `false`, `no`, `off`. Any
other non-empty value causes the script to print a diagnostic to stderr and
exit with code 2.

### Design Rationale

Prior to `common.py` each script declared its own inline `_required_env`
helper, leading to divergent exit codes and error messages. Centralizing the
helper ensures that a missing required variable always produces a message of
the form `Missing required environment variable: NAME` and exits with code 2,
regardless of which script is running.

Boolean environment variables similarly used Typer's built-in `envvar` binding,
which silently accepted any non-empty string as truthy. `_env_bool` replaces
that path to provide an explicit rejection of unrecognized values.

## Roadmap

- [x] Centralize environment-variable parsing helpers (`_required_env`,
  `_env_bool`) into `common.py` so error messages and exit codes are consistent
  across all generate-coverage scripts.
- [x] Extend artefact naming to include platform metadata and support custom
  suffixes.
- [x] Document the Rust coverage environment-override design for
  Cranelift-configured repositories.
- [x] Replace `uv run --with` ephemeral environments with a persistent,
  job-local `.venv-coverage` virtual environment to enable intra-process
  caching of the Python interpreter path via `functools.lru_cache` and to add
  broken-venv recovery.

## Python Coverage Venv Architecture

### Motivation

Running `uv run --with slipcover ...` on each invocation re-resolves
dependencies and creates a temporary environment on every call. A named venv
(`.venv-coverage`) is created once per job, reused on subsequent calls within
the same job, and discarded when the runner workspace is cleaned up.

### Lifecycle

1. `_ensure_coverage_venv()` checks whether `.venv-coverage` contains a Python
   executable.
   - If absent, it creates the venv via `uv venv .venv-coverage`.
   - If present but broken (Python binary missing), it removes the existing
     path - unlinking files and symlinks and removing directories - and then
     recreates it.
2. `_ensure_coverage_venv()` syncs the current project into the venv with
   `uv sync --inexact --python <venv-python>` so tests can import project
   dependencies.
3. `_ensure_coverage_venv()` performs installation of `slipcover>=1.0.18`,
   `pytest`, `pytest-xdist`, and `coverage` into the venv using
   `uv pip install --python <venv-python>`. The `--system` flag is deliberately
   excluded to keep the installation isolated. The `slipcover>=1.0.18` floor
   forces uv to upgrade any older slipcover already installed by `uv sync`, so
   the xdist plugin needed for `pytest -n <workers>` coverage merging is
   guaranteed present.
4. `_coverage_python_cmd()` calls `_ensure_coverage_venv()` on first use, caches
   the resulting `plumbum` command via `functools.lru_cache`, and returns the
   cached value on all subsequent calls within the same process.

`<venv-python>` is the absolute path to the Python executable inside
`.venv-coverage`, not the result of resolving that executable through symlinks.
This distinction matters on Linux because venv Python executables commonly
symlink to the base interpreter. Resolving the symlink before invoking uv would
redirect installs back to the system Python and defeat the isolation provided by
`.venv-coverage`.

### Concurrency Model

GitHub Actions executes action steps sequentially in a single thread. The
`functools.lru_cache` memoized `_coverage_python_cmd()` accessor therefore
requires no explicit synchronization.

### Coverage Venv API

<!-- markdownlint-disable MD013 MD060 -->
| Symbol                                   | Role                                                                                   |
| ---------------------------------------- | -------------------------------------------------------------------------------------- |
| `_ensure_coverage_venv() -> str`         | Create or recover the venv, install project/tool dependencies, and return Python path. |
| `_coverage_python_cmd() -> BoundCommand` | Return the cached venv Python command.                                                 |
<!-- markdownlint-enable MD013 MD060 -->

## Addendum: prebuilt CI tool installation (2026-09-03)

`cargo-llvm-cov` and `cargo-nextest` now follow separate installation
strategies. `cargo-llvm-cov` continues to install via a pinned `cargo-binstall`.
`cargo-nextest` instead downloads its pinned official release archive directly
from `nextest-rs/nextest` and never invokes Cargo, so a missing prebuilt binary
is a hard error rather than a source build.

### Platform asset selection

`install_cargo_nextest.py` pins the target release with `CARGO_NEXTEST_VERSION`
and resolves a `ReleaseAsset` for the runner from
`CARGO_NEXTEST_RELEASE_ASSETS`, keyed by `_platform_key()`. That key covers
`linux-x86_64-gnu`, `linux-x86_64-musl`, `linux-aarch64-gnu`, `mac-universal`,
`windows-x86_64`, and `windows-aarch64`. `_platform_key()` distinguishes the
two Linux x86_64 and Linux aarch64 libc variants by delegating to `_is_musl()`,
which loads the runner's libc through `ctypes.CDLL` and treats the absence of
`gnu_get_libc_version` as musl. An unresolved key raises `typer.Exit(1)` from
`_release_for_platform()`, so an unsupported platform fails the installer
immediately.

### Two-stage digest verification

Each `ReleaseAsset` carries the expected archive SHA-256, and
`CARGO_NEXTEST_SHA256` separately pins the expected digest of the extracted
executable for each platform key. `install_cargo_nextest()` downloads the
archive, hashes it with `_sha256_path()`, and compares that hash against
`asset.sha256` before extraction proceeds. `_extract_binary()` then pulls only
the `cargo-nextest` (or `cargo-nextest.exe`) member out of the archive, and
`verify_nextest_binary()` hashes the extracted file and compares it against the
platform's pinned executable digest. Either mismatch raises `typer.Exit(1)` and
aborts installation.

### Atomic replacement

`install_cargo_nextest()` extracts the verified executable to a temporary path,
`destination.with_suffix(f"{destination.suffix}.tmp")`, inside the Cargo `bin`
directory rather than writing straight to `destination`. Only after both digest
checks succeed does it `chmod` the temporary file and call
`temporary_binary.replace(destination)`, which performs an atomic rename on the
same filesystem. A `finally` block removes any leftover temporary file.
Consequently, a failure at any earlier stage — download, archive digest,
extraction, or binary digest — leaves a previously installed `cargo-nextest`
binary at `destination` untouched.

### No source-fallback policy

The installer never calls `cargo install cargo-nextest` or any other
source-building command, on success or on failure. This mirrors the
`install-whitaker` action's approach and follows the Namespace runner adoption
recipe, which forbids a CI tool installer that can silently fall back to
compiling a tool from source: a source build on a Namespace runner would defeat
the point of using prebuilt, digest-verified binaries and could introduce a
much slower, non-reproducible installation path with a different provenance to
the pinned release.
