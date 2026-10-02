# Migrating to `setup-rust`'s `install-clang-lld`

This guide covers the `install-clang-lld` input and `clang-lld-status` output
that `setup-rust` gains in its next minor tag. The change is additive: nothing
changes for a caller that does not pass the input. Read this to replace a
handwritten `apt-get install clang lld` step.

## What changed

`setup-rust` can now install clang and lld on Linux runners. With
`install-clang-lld: 'true'` it runs `apt-get update` and
`apt-get install --yes --no-install-recommends clang lld`, then fails unless
both `clang` and `ld.lld` are on `PATH`. On macOS and Windows it prints a
notice and installs nothing, so a matrix can pass the input to every leg.

The `clang-lld-status` output is `installed` or `skipped`, and empty when the
input is not `true`.

## What did not change

The action sets no linker flag. Select clang and lld through the project's
Cargo configuration, or through `CARGO_TARGET_<triple>_LINKER` and `RUSTFLAGS`,
exactly as before. An existing coverage environment stays as it is.

## How to migrate

Before:

```yaml
- uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
- name: Install mold linker
  if: runner.os == 'Linux'
  run: |
    sudo apt-get update
    sudo apt-get install --yes --no-install-recommends clang lld mold
```

After:

```yaml
- uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
  with:
    install-mold: 'true'
    install-clang-lld: 'true'
```

Delete the `apt-get` step, and pin `setup-rust` to a commit that includes
`install-clang-lld`. Keep any existing linker selection.
