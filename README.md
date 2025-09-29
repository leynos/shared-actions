# shared-actions

GitHub Actions

## Available actions

| Name                      | Path                                        | Latest major |
| ------------------------- | ------------------------------------------- | ------------ |
| Ensure Cargo version      | `.github/actions/ensure-cargo-version`      | v1           |
| Export Postgres URL       | `.github/actions/export-postgres-url`       | v1           |
| Generate coverage         | `.github/actions/generate-coverage`         | v1           |
| Setup Rust                | `.github/actions/setup-rust`                | v1           |
| Release to PyPI (uv)      | `.github/actions/release-to-pypi-uv`        | v1           |
| Upload CodeScene Coverage | `.github/actions/upload-codescene-coverage` | v1           |
| Ratchet coverage          | `.github/actions/ratchet-coverage`          | v1           |
| Rust build release        | `.github/actions/rust-build-release`        | v1           |
| Linux packages            | `.github/actions/linux-packages`            | v1           |
| macOS package             | `.github/actions/macos-package`             | v1           |
| Windows package           | [`./.github/actions/windows-package`](./.github/actions/windows-package/README.md) | v0           |

## Development

Format, validate and test the repository:

```sh
make fmt
make test
```