# shared-actions

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/shared-actions)

GitHub Actions

## Available actions

| Name                      | Path                                                                               | Latest major |
| ------------------------- | ---------------------------------------------------------------------------------- | ------------ |
| Determine release modes   | `.github/actions/determine-release-modes`                                          | v1           |
| Ensure Cargo version      | `.github/actions/ensure-cargo-version`                                             | v1           |
| Export Cargo metadata     | `.github/actions/export-cargo-metadata`                                            | v1           |
| Export Postgres URL       | `.github/actions/export-postgres-url`                                              | v1           |
| Generate coverage         | `.github/actions/generate-coverage`                                                | v1           |
| Install Nixie             | `.github/actions/install-nixie`                                                    | unreleased   |
| Linux packages            | `.github/actions/linux-packages`                                                   | v1           |
| macOS package             | `.github/actions/macos-package`                                                    | v1           |
| Ratchet coverage          | `.github/actions/ratchet-coverage`                                                 | v1           |
| Release to PyPI (uv)      | `.github/actions/release-to-pypi-uv`                                               | v1           |
| Rust build release        | `.github/actions/rust-build-release`                                               | v1           |
| Resolve workflow source   | `.github/actions/resolve-workflow-source`                                          | unreleased   |
| Setup Rust                | `.github/actions/setup-rust`                                                       | v1           |
| Stage release artefacts   | `.github/actions/stage-release-artefacts`                                          | v1           |
| Upload CodeScene Coverage | `.github/actions/upload-codescene-coverage`                                        | v1           |
| Upload release assets     | `.github/actions/upload-release-assets`                                            | v1           |
| Validate Linux packages   | `.github/actions/validate-linux-packages`                                          | v1           |
| Windows package           | [`./.github/actions/windows-package`](./.github/actions/windows-package/README.md) | v0           |

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
