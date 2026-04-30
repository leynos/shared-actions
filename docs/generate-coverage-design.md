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
  `coverage`) via `uv pip install --python`, and `_coverage_python_cmd()` caches
  the resulting interpreter command for the lifetime of the process.
- *2026-04-30* — Python coverage venv discovery now preserves the absolute
  venv interpreter path instead of resolving it through symlinks. On Linux,
  `.venv-coverage/bin/python` can point at `/usr/bin/python3.12`; passing the
  resolved target to `uv pip install --python` makes uv treat `/usr` as the
  install environment and trips externally-managed-interpreter protections.
  The action must pass the venv path itself to uv, and it logs the candidate
  paths, resolved targets, and selected uv command interpreter for diagnosis.

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
and non-coverage flows continue to use Cranelift if the repository asked for
it.

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
prevents a caller or runner host from
leaking an unrelated Cranelift preference into coverage runs.

### Cranelift Detection Strategy

Cranelift detection is intentionally lightweight, but it checks two
sources before deciding coverage needs LLVM overrides:

- `_uses_cranelift_backend(manifest_path)` walks upward from the selected Cargo
  manifest directory and scans `.cargo/config.toml` plus `.cargo/config`.
- `_manifest_uses_cranelift_backend(manifest_path)` reads the selected
  `Cargo.toml`.
- The selected manifest is scanned for profile sections containing
  `codegen-backend = "cranelift"` or the single-quoted equivalent.

The action therefore catches repository-level Cargo config overrides and
per-manifest profile settings using two lightweight text scans: `.cargo/config*`
detection stays regex-based, while `_manifest_uses_cranelift_backend()` walks
the selected `Cargo.toml` line by line and checks only `[profile]` sections.

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

## Roadmap

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
3. `_ensure_coverage_venv()` performs installation of `slipcover`, `pytest`,
   and `coverage` into the venv using
   `uv pip install --python <venv-python>`. The `--system` flag is deliberately
   excluded to keep the installation isolated.
4. `_coverage_python_cmd()` calls `_ensure_coverage_venv()` on first use, caches
   the resulting `plumbum` command via `functools.lru_cache`, and returns the
   cached value on all subsequent calls within the same process.

`<venv-python>` is the absolute path to the Python executable inside
`.venv-coverage`, not the result of resolving that executable through symlinks.
This distinction matters on Linux because venv Python executables commonly
symlink to the base interpreter. Resolving the symlink before invoking uv
would redirect installs back to the system Python and defeat the isolation
provided by `.venv-coverage`.

### Concurrency Model

GitHub Actions executes action steps sequentially in a single thread. The
`functools.lru_cache` memoized `_coverage_python_cmd()` accessor therefore
requires no explicit synchronization.

### Public API

<!-- markdownlint-disable MD013 MD060 -->
| Symbol | Role |
|---|---|
| `_ensure_coverage_venv() -> str` | Create or recover the venv, install project/tool dependencies, and return Python path. |
| `_coverage_python_cmd() -> BoundCommand` | Return the cached venv Python command. |
<!-- markdownlint-enable MD013 MD060 -->
