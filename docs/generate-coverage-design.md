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

Using `CARGO_PROFILE_*_CODEGEN_BACKEND` matches Cargo's configuration model
while keeping the override tightly scoped to the coverage subprocess. The
repository's checked-in configuration stays unchanged, and non-coverage flows
continue to use Cranelift if the repository asked for it.

### Where the Overrides Apply

The overrides are applied in both Rust coverage entry points:

- the main `cargo llvm-cov` run
- the optional cucumber.rs follow-up run when `with-cucumber-rs` is enabled

Both paths call the same `get_cargo_coverage_env()` helper so the logic does
not drift between the primary and follow-up coverage invocations.

### Behaviour of `env_overrides`

`_run_cargo(..., env_overrides=...)` accepts an optional mapping:

- `None` means "use the inherited process environment unchanged"
- a mapping means "merge the current environment with these extra keys"

The helper does not construct a clean-room environment. It starts from
`os.environ` and overlays the requested keys, preserving the rest of the
workflow's runtime context such as PATH, toolchain configuration, and any
GitHub Actions environment variables already set by the caller.

### Cranelift Detection Strategy

Cranelift detection deliberately reuses the existing lightweight search in
`_uses_cranelift_backend(manifest_path)`:

- start from the selected Cargo manifest directory
- walk upward towards the filesystem root
- look for `.cargo/config.toml` and `.cargo/config`
- read the file as UTF-8 when possible
- treat any line matching `codegen-backend = "cranelift"` as a signal that
  coverage should switch the `dev` and `test` profiles back to LLVM

This approach is fast, dependency-free, and good enough for the specific
coverage failure mode the action is addressing.

### Known Limitations

The current detection path is intentionally simple and has limits developers
should understand:

- It is text-based rather than TOML-structure-aware. Any matching
  `codegen-backend = "cranelift"` assignment triggers the override, even if it
  appears in a table that is more specific than the action strictly needs to
  reason about.
- It only inspects `.cargo/config.toml` and `.cargo/config` files reachable by
  walking upward from the manifest directory. It does not model configuration
  injected via CLI `--config`, environment-backed Cargo config, or other
  runtime indirection.
- It always applies both `CARGO_PROFILE_DEV_CODEGEN_BACKEND` and
  `CARGO_PROFILE_TEST_CODEGEN_BACKEND` together once Cranelift is detected. The
  action currently does not try to mirror per-profile granularity from the
  repository config.
- Files that cannot be read as UTF-8 are ignored rather than failing the run,
  because the action prefers a conservative fallback over blocking coverage for
  an unrelated config parse issue.

If the repository ever needs finer-grained handling, the next step would be a
real TOML parser plus table-aware resolution. The current design intentionally
stops short of that complexity.

## Roadmap

- [x] Extend artefact naming to include platform metadata and support custom
  suffixes.
- [x] Document the Rust coverage environment-override design for
  Cranelift-configured repositories.
