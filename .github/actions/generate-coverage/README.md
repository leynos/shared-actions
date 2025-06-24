# Generate coverage

Run code coverage for Rust or Python projects. The action uses
`cargo llvm-cov` when a `Cargo.toml` is present and `slipcover` with
`pytest` when a `pyproject.toml` is present. It installs `slipcover` and
`pytest` automatically via `uv` before running the tests. If both
configuration files are found, the action fails.

## Flow

```mermaid
flowchart TD
    A[Start] --> B{Project type?}
    B -- Cargo.toml present --> C[Set lang=rust]
    B -- pyproject.toml present --> D[Set lang=python]
    B -- Neither present --> E[Exit with error]
    C --> F{lang}
    D --> F
    F -- rust --> G[Run cargo llvm-cov]
    F -- python --> H[Run slipcover with pytest]
    G --> I[Set outputs: file, format]
    H --> I
    I --> J[End]
```

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| features | Cargo features to enable (Rust) | no | |
| with-default-features | Enable default Cargo features (Rust) | no | `true` |
| output-path | Output file path | yes | |
| format | Coverage format (`lcov`*, `cobertura` or `coveragepy`) | no | `cobertura` |

\* `lcov` is only supported for Rust projects.

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
