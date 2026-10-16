# shared-actions

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/shared-actions)

GitHub Actions

## Available actions

| Name                              | Path                                                                               | Latest major |
| --------------------------------- | ---------------------------------------------------------------------------------- | ------------ |
| Determine release modes           | `.github/actions/determine-release-modes`                                          | v1           |
| Ensure Cargo version              | `.github/actions/ensure-cargo-version`                                             | v1           |
| Export Cargo metadata             | `.github/actions/export-cargo-metadata`                                            | v1           |
| Export Postgres URL               | `.github/actions/export-postgres-url`                                              | v1           |
| Export Ubicloud cache credentials | `.github/actions/export-ubicloud-cache-credentials`                                | unreleased   |
| Generate coverage                 | `.github/actions/generate-coverage`                                                | v1           |
| Install makeutil                  | `.github/actions/install-makeutil`                                                 | unreleased   |
| Install mdtablefix                | `.github/actions/install-mdtablefix`                                               | unreleased   |
| Install Nixie                     | `.github/actions/install-nixie`                                                    | unreleased   |
| Install tool                      | `.github/actions/install-tool`                                                     | unreleased   |
| Install YAML lint tools           | `.github/actions/install-yaml-lint`                                                | unreleased   |
| Linux packages                    | `.github/actions/linux-packages`                                                   | v1           |
| macOS package                     | `.github/actions/macos-package`                                                    | v1           |
| MSRV check                        | `.github/actions/msrv-check`                                                       | unreleased   |
| Ratchet coverage                  | `.github/actions/ratchet-coverage`                                                 | v1           |
| Release to PyPI (uv)              | `.github/actions/release-to-pypi-uv`                                               | v1           |
| Rust build release                | `.github/actions/rust-build-release`                                               | v1           |
| Resolve workflow source           | `.github/actions/resolve-workflow-source`                                          | unreleased   |
| Setup Rust                        | `.github/actions/setup-rust`                                                       | v1           |
| Report sccache statistics         | `.github/actions/sccache-report`                                                   | unreleased   |
| Stage release artefacts           | `.github/actions/stage-release-artefacts`                                          | v1           |
| Upload CodeScene Coverage         | `.github/actions/upload-codescene-coverage`                                        | v1           |
| Upload release assets             | `.github/actions/upload-release-assets`                                            | v1           |
| Validate Linux packages           | `.github/actions/validate-linux-packages`                                          | v1           |
| Windows package                   | [`./.github/actions/windows-package`](./.github/actions/windows-package/README.md) | v0           |

See the [Install Nixie guide](docs/users-guide.md#install-nixie) for usage,
version inputs, and failure behaviour.

## Checking a minimum supported Rust version

A crate that declares `rust-version` promises its users a compiler floor, and
the estate's `rust-toolchain-policy` rule (RT-026) asks that CI exercises it.
The `msrv-check` action does so with one job: it reads `rust-version` from the
manifest (so the workflow never repeats the version), installs that toolchain,
drops the runner checkout's development `.cargo/config.toml`, and runs one
`cargo check --lib --locked --all-features`. See
`.github/actions/msrv-check/README.md` for the inputs and the behaviour.

```yaml
msrv:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@<sha>
      with:
        persist-credentials: false
    - uses: leynos/shared-actions/.github/actions/msrv-check@<sha>
```

## Vendored helpers

These are scripts that a repository copies, not published actions.

- [`uv_gate`](uv_gate/README.md): runs `uv` offline-first with one bounded
  online step; see the
  [users' guide](docs/users-guide.md#uv_gate-the-vendored-uv-helper).

## Reusable workflows

| Name                             | Path                                         | Guide                                                                          |
| -------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------ |
| Dependabot auto-merge            | `.github/workflows/dependabot-automerge.yml` | [docs/dependabot-automerge-workflow.md](docs/dependabot-automerge-workflow.md) |
| Mutation testing (cargo-mutants) | `.github/workflows/mutation-cargo.yml`       | [docs/mutation-cargo-workflow.md](docs/mutation-cargo-workflow.md)             |
| Mutation testing (mutmut)        | `.github/workflows/mutation-mutmut.yml`      | [docs/mutation-mutmut-workflow.md](docs/mutation-mutmut-workflow.md)           |

## Development

Format, validate and test the repository:

```sh
make fmt
make test
```
