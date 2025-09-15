# shared-actions

GitHub Actions

## Available actions

| Name | Path | Latest major |
| ---- | ---- | ------------ |
| Export Postgres URL | `.github/actions/export-postgres-url` | v1 |
| Generate coverage | `.github/actions/generate-coverage` | v1 |
| Setup Rust | `.github/actions/setup-rust` | v1 |
| Upload CodeScene Coverage | `.github/actions/upload-codescene-coverage` | v1 |
| Ratchet coverage | `.github/actions/ratchet-coverage` | v1 |
| Rust build release | `.github/actions/rust-build-release` | v1 |

## Development

Format, validate and test the repository:

```sh
make fmt
make test
```
