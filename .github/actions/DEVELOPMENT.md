# Developer Guide

This document explains the internal action architecture added across the
toolchain-selection and coverage-overrides work on `rust-build-release` and
`generate-coverage`.

## Toolchain Resolution (rust-build-release)

`rust-build-release` resolves the build toolchain in four levels, from most
specific to least specific.

1. The explicit `toolchain` input passed to the action.
2. The nearest `rust-toolchain.toml` or legacy `rust-toolchain` file found by
   walking upward from the manifest directory toward the repository boundary.
3. `package.rust-version` from the manifest, or
   `workspace.package.rust-version` for workspace manifests.
4. The action's bundled `TOOLCHAIN_VERSION` file.

`resolve_requested_toolchain` is the public entry point for this precedence
chain. It trims the explicit input first, then falls back through the
repository toolchain, the manifest MSRV, and finally the bundled default.

`read_repo_toolchain` handles repository-level discovery. It resolves the
manifest path relative to the project directory, starts from the manifest's
parent directory, and returns the first matching toolchain declaration it finds.

`_iter_toolchain_search_dirs` performs the upward walk. It yields each search
directory in order, stopping at the first `.git` directory it encounters, at
the filesystem root, or at the optional `stop_at` boundary when one is
provided. `read_repo_toolchain` passes the project directory as that boundary so
the search stays inside the checked-out repository.

`_parse_toolchain_file` reads each candidate file. It parses TOML
`rust-toolchain.toml` files via `tomllib`, and only falls back to the legacy
line-based format for files literally named `rust-toolchain`.

`read_manifest_rust_version` is the manifest-level fallback. It loads the Cargo
manifest, checks `package.rust-version`, and if that is absent checks
`workspace.package.rust-version`.

`read_default_toolchain` is the final fallback. It reads the action's
`TOOLCHAIN_VERSION` file and returns that bundled default string unchanged.

The result of this chain is used both by the action setup helper and by the
runtime build path, so explicit overrides, repository declarations, direct
Python entry points, and CLI invocation all follow the same resolution model.

## Cranelift Coverage Override (generate-coverage)

`cargo llvm-cov` requires LLVM-backed compilation. Projects that enable the
Cranelift backend for development or test profiles can compile normally with
plain Cargo, but `cargo llvm-cov` and the child Cargo commands it spawns cannot
produce usable coverage data with Cranelift enabled.

`_uses_cranelift_backend(manifest_path)` is the detection entry point. It first
checks the manifest itself through `_manifest_uses_cranelift`, which looks for
`[profile.*].codegen-backend` entries in `Cargo.toml`. If the manifest does not
declare Cranelift, it then walks upward from the manifest directory and scans
`.cargo/config.toml` and `.cargo/config` in each directory.

`get_cargo_coverage_env(manifest_path)` converts that detection result into the
environment overrides used for coverage runs. It returns an empty mapping for
normal projects. For Cranelift-configured projects it returns a copy of the
override environment containing `CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm` and
`CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm`.

`_run_cargo(args, *, extra_env)` is the subprocess boundary that applies those
overrides. It calls `_build_cargo_command(extra_env)`, which uses
`cargo.with_env(**extra_env)` when overrides are present. That means the
coverage environment is attached to the `cargo llvm-cov` process itself rather
than being passed as outer `cargo --config` flags. Because the environment is
part of the process state, child Cargo invocations spawned by `cargo llvm-cov`
inherit the LLVM backend override automatically.

This is the important distinction: the action does not try to rewrite profile
settings in the command line. It forces the two Cargo profile environment
variables at process launch so both `cargo llvm-cov` and its child Cargo
processes stay on LLVM for the duration of the coverage run.

## Adding a New Action

Keep the action self-contained under `.github/actions/<action-name>/` with its
own `action.yml`, `README.md`, tests, and `CHANGELOG.md`.

Use test fixtures when the action needs realistic manifests, workflow files,
archives, or directory trees. Keep those fixtures close to the tests, usually
under `tests/fixtures/`, and prefer small, purpose-built examples over copying
large real projects.

Update the action-local `CHANGELOG.md` for user-visible behavior changes. The
repository uses per-action changelogs rather than one shared release log.

Keep the action `README.md` complete. At minimum it should explain what the
action does, list inputs and outputs, show an example usage block, and document
behavioral details that users need in order to debug configuration-sensitive
paths such as toolchain resolution or coverage overrides.

When you add or change action logic, make the docs and fixtures move together.
The README should describe externally visible behavior, the changelog should
record the release-facing change, and the fixtures should cover the branch that
made the change necessary.
