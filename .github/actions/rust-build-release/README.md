# rust-build-release

Build Rust application release artefacts.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| target | string | `""` | Target triple to build | no |

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
```

## Release History

See [CHANGELOG](CHANGELOG.md).
