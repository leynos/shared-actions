# rust-build-release

Build Rust application release artefacts using the repository's `setup-rust` action, `uv`, and `cross`. Linux packaging is delegated to the [`linux-packages`](../linux-packages) composite action.

The `uv` Python package manager is installed automatically to execute the build
script.

## Inputs

| Name        | Type   | Default                    | Description                                | Required |
| ----------- | ------ | -------------------------- | ------------------------------------------ | -------- |
| target      | string | `x86_64-unknown-linux-gnu` | Target triple to build                     | no       |
| project-dir | string | `.`                        | Path to the Rust project to build          | no       |
| bin-name    | string | `rust-toy-app`             | Binary name to stage and package           | no       |
| formats     | string | `deb`                      | Comma-separated package formats to produce | no       |

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    formats: deb,rpm

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    formats: deb
```

## Release History

See [CHANGELOG](CHANGELOG.md).
