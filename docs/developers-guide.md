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
