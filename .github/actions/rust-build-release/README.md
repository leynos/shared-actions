# rust-build-release

Build Rust application release artifacts.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| target | string | `""` | Target triple to build | no |

## Outputs

None.

## Usage

```yaml
- uses: ./.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
```

## Release History

See [CHANGELOG](CHANGELOG.md).
