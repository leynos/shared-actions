# ensure-cargo-version

Validate that the Git tag triggering a release workflow matches the version in one or more Cargo manifests.

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `manifests` | No | `Cargo.toml` | Newline or whitespace separated list of Cargo manifest paths to check. Paths are resolved relative to the GitHub workspace. |

## Outputs

| Name | Description |
| ---- | ----------- |
| `version` | Version extracted from the tag reference (leading `v` removed). |

## Usage

```yaml
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Ensure Cargo versions match tag
        uses: ./.github/actions/ensure-cargo-version
        with:
          manifests: |
            Cargo.toml
            crates/secondary/Cargo.toml
```
