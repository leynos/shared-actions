# Development

## argv assembly pattern

`rust-build-release` builds process invocations as plain argv lists before
handing them to plumbum. `_build_cross_command` assembles the final `cross`
argv, including manifest, release, target, and feature arguments, validates that
list, and then resolves `executor[cmd[1:]]` because plumbum already supplies the
executable. `_build_cargo_command` follows the same list-first pattern for
`cargo`, inserting the configured cargo toolchain override only after all normal
arguments have been assembled.

This keeps ordering explicit, makes tests assert the exact command shape, and
avoids hidden post-construction mutations of plumbum command objects.

## Cross command validation guard

`_assert_cross_command_has_no_toolchain_override` rejects any `cross` argv that
contains a `+<toolchain>` argument after the executable. `cross` must not
receive a rustup toolchain override in argv; the toolchain is controlled by
`rust-toolchain.toml` or `RUSTUP_TOOLCHAIN` instead.

The guard raises `ValueError` so command construction fails before execution.
`main()` catches that error, emits a GitHub Actions `::error::` annotation that
includes the affected target, and exits with status 1.

## Makefile tool discovery

The Makefile resolves `ACTION_VALIDATOR` and `RUFF` from candidate path lists
before falling back to the variable value provided by the caller. The action
validator candidates include common Cargo, Bun, and system installation paths.
The Ruff candidates include the local virtual environment, user, and system
installation paths.

This lets CI and local developer machines find tools even when their package
manager does not place shims on `PATH`.

## gha adapter module

`src/gha.py` provides three thin wrappers — `debug`, `warning`, and `error` --
that prepend the appropriate GitHub Actions workflow command prefix
(`::debug::`, `::warning::`, `::error::`) before delegating to an injected
`echo` callable (defaulting to `typer.echo`). All annotation emission in
`main.py` is routed through this module so the formatting is consistent and
testable.

<!-- markdownlint-disable-next-line MD037 -->
## _assemble_build_command and _check_target_support helpers

`_assemble_build_command` is a pure query: it returns either `(cmd, None)` on
success or `(None, error_message)` on validation failure. All side-effects
(annotation emission and process exit) are the responsibility of `main()`.

`_check_target_support` checks whether a given toolchain can build a requested
target without cross; it raises `typer.Exit(1)` when the target is unsupported
and cross is disabled. This side-effect is acceptable here because target
support is a hard precondition, not a recoverable error.

## ValueError-to-annotation flow

When `_build_cross_command` raises `ValueError` (e.g. because the guard
detects a `+<toolchain>` token), `_assemble_build_command` returns the error
message as the second tuple element. `main()` then calls `gha_error` with that
message and raises `typer.Exit(1)`. This keeps the domain logic free of
framework concerns whilst preserving rich error context in the annotation.

## _CommandWrapper refactoring

`_CommandWrapper` was refactored to be a pure value object: it holds no
injected echo callable, and `formulate()` is a side-effect-free query. A
`_validate_formulation` module-level helper raises `TypeError` when the
wrapped command does not expose a callable `formulate()`, and is called from
`__init__` and `with_env`.

## Test strategy

The test suite uses:

- **pytest** for unit and integration tests.
- **Hypothesis** (`hypothesis>=6.100`) for property-based tests that validate
  the `+<toolchain>` guard invariant across randomised argv shapes.
- **syrupy** (`syrupy>=4.0`) for snapshot tests that record the exact
  `::debug:: cross argv:` line emitted by `main()`, ensuring format stability
  across refactors.
- **`unittest.mock.patch`** as a wrapping spy to verify the guard is called
  exactly once during `_build_cross_command` without replacing its behaviour.
