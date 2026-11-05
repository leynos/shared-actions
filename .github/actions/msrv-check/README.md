# msrv-check

Check a crate on its declared minimum supported Rust version (MSRV). The action
reads `rust-version` from the manifest, installs that toolchain, and runs one
`cargo check`. The version is never written into the workflow, so the manifest
remains the only place it is declared.

The default check is `cargo check --lib --locked --all-features`: one compile
of the library on the locked dependencies, not a second test run. It runs on
the cheapest runner and adds no wall-clock time when it runs beside the main
job.

## Inputs

| Name                | Type   | Required | Default                         | Description                                                           |
| ------------------- | ------ | -------- | ------------------------------- | --------------------------------------------------------------------- |
| `manifest-path`     | string | no       | `Cargo.toml`                    | Cargo.toml that declares `rust-version`, inside the checkout          |
| `working-directory` | string | no       | `.`                             | Directory the check runs in                                           |
| `cargo-args`        | string | no       | `--lib --locked --all-features` | Arguments for `cargo check`, split with shell quoting rules           |
| `keep-cargo-config` | string | no       | `false`                         | Keep the repository's `.cargo/config.toml` (`true`, `1`, `yes`, `on`) |

## Outputs

| Name   | Description                          |
| ------ | ------------------------------------ |
| `msrv` | The `rust-version` the check ran on. |

## Behaviour

- A manifest with no `rust-version`, directly or through its workspace
  (a member inherits the workspace value only when it writes
  `rust-version.workspace = true`, as Cargo does), fails the job with a named
  error before any toolchain is installed.
- By default the action drops `.cargo/config.toml` (and the legacy
  `.cargo/config`) from the **runner's checkout** for this job and sets an empty
  `RUSTFLAGS`. A development config usually names nightly-only keys such as a
  codegen backend, or a linker such as mold that this job does not install, and
  an assigned `RUSTFLAGS` replaces the config's rustflags. Nothing is
  committed. Set `keep-cargo-config: true` to keep both, for a repository whose
  config is valid on the MSRV.
- `cargo +<version>` selects the toolchain over any `rust-toolchain.toml`, so
  the pin file needs no removal.
- Each dropped file is logged as a notice.

## Usage

```yaml
msrv:
  runs-on: ubuntu-latest
  permissions:
    contents: read
  steps:
    - uses: actions/checkout@<sha>
      with:
        persist-credentials: false
    - uses: leynos/shared-actions/.github/actions/msrv-check@<sha>
```

For a workspace, name the member and the packages:

```yaml
- uses: leynos/shared-actions/.github/actions/msrv-check@<sha>
  with:
    manifest-path: crates/core/Cargo.toml
    cargo-args: -p core --lib --locked --all-features
```

The `concordat` rule `rust-toolchain-policy` (RT-026) recognizes a step that
uses this action as exercising the declared MSRV.

## Release history

See [CHANGELOG](CHANGELOG.md).
