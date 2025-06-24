# Generate coverage

Run code coverage for Rust or Python projects. The action uses
`cargo llvm-cov` when a `Cargo.toml` is present and `slipcover` with
`pytest` when a `pyproject.toml` is present. If both files are found the
action fails.

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| features | Cargo features to enable (Rust) | no | |
| with-default-features | Enable default Cargo features (Rust) | no | `true` |
| output-path | Output file path | yes | |
| format | Coverage format (`lcov`, `cobertura` or `coveragepy`) | no | `cobertura` |

## Outputs

| Name | Description |
| --- | --- |
| file | Path to the generated coverage file |
| format | Format of the coverage file |

## Example

```yaml
- uses: ./.github/actions/generate-coverage@v1
  with:
    output-path: coverage.xml
    format: cobertura
```

Release history is available in [CHANGELOG](CHANGELOG.md).
