# Export Cargo Metadata

Extract metadata from the Cargo.toml (Tom's Obvious, Minimal Language, TOML)
manifest and export as GitHub Actions outputs and environment variables.

## Inputs

| Name | Description | Required | Default |
| ---- | ----------- | -------- | ------- |
| `manifest-path` | Path to Cargo.toml | no | `Cargo.toml` |
| `fields` | Comma-separated list of fields to extract | no | `name,version` |
| `export-to-env` | Also export fields to GITHUB_ENV | no | `true` |

## Outputs

| Name | Description |
| ---- | ----------- |
| `name` | Package name from `[package].name` |
| `version` | Package version from `[package].version` (resolves workspace inheritance) |
| `bin-name` | Binary name from first `[[bin]].name` or `[package].name` |
| `description` | Package description from `[package].description` |

## Usage

### Basic usage

```yaml
- uses: ./.github/actions/export-cargo-metadata
  id: cargo
  with:
    manifest-path: Cargo.toml

- name: Use extracted metadata
  run: |
    echo "Package: ${{ steps.cargo.outputs.name }}"
    echo "Version: ${{ steps.cargo.outputs.version }}"
```

### Extract specific fields

```yaml
- uses: ./.github/actions/export-cargo-metadata
  with:
    fields: name,version,bin-name,description
```

### Use environment variables in subsequent steps

When `export-to-env: true` (the default), fields are also exported to
`GITHUB_ENV` as uppercase variables:

```yaml
- uses: ./.github/actions/export-cargo-metadata
  with:
    fields: name,version

- name: Use environment variables
  run: |
    echo "NAME=${NAME}"
    echo "VERSION=${VERSION}"
```

### Remote usage

```yaml
- uses: leynos/shared-actions/.github/actions/export-cargo-metadata@v1
  with:
    manifest-path: crates/cli/Cargo.toml
    fields: name,version,bin-name
```

## Behaviour

- **Manifest resolution**: Paths are resolved relative to `GITHUB_WORKSPACE`
  unless absolute.

- **Workspace inheritance**: When `[package].version` uses
  `version.workspace = true`, the action searches for the workspace root and
  resolves the version from `[workspace.package].version`.

- **Binary name detection**: The `bin-name` field checks for the first
  `[[bin]].name` entry and falls back to `[package].name` if no explicit
  binary is declared.

- **Environment export**: When `export-to-env: true`, each extracted field is
  written to `GITHUB_ENV` using an uppercase variable name (e.g., `name` →
  `NAME`, `bin-name` → `BIN_NAME`).

## Release History

See [CHANGELOG](CHANGELOG.md).
