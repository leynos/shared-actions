# rust-build-release

Build Rust application release artefacts using the repository's
`setup-rust` action and `cross`.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| target | string | `x86_64-unknown-linux-gnu` | Target triple to build | yes |
| project-dir | string | `.` | Path to the Rust project to build | no |

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
```

## Release History

See [CHANGELOG](CHANGELOG.md).
