# Ratchet coverage

Generate code coverage using `cargo tarpaulin` and fail the workflow if the
coverage percentage falls below a stored baseline.

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| baseline-file | File used to persist the baseline coverage percentage between runs | no | `.coverage-baseline` |
| args | Additional arguments passed to `cargo tarpaulin` | no | *(none)* |

## Outputs

| Name | Description |
| --- | --- |
| percent | Coverage percentage reported by `cargo tarpaulin` |

## Example

```yaml
- uses: ./.github/actions/setup-rust@v1
- uses: ./.github/actions/ratchet-coverage@v1
  with:
    baseline-file: .cache/coverage-baseline
    args: --workspace
```

`cargo tarpaulin` only runs on Linux hosts, so use this action on
`ubuntu-latest` runners.

### How it works

The action restores the previous coverage baseline using `actions/cache` and
installs `cargo-tarpaulin` if necessary. After running the coverage command, it
compares the new percentage with the stored baseline. The job fails if coverage
has dropped. On success, the baseline file is updated and saved back to the
cache using a branch-specific key for future runs.

Release history is available in [CHANGELOG](CHANGELOG.md).
