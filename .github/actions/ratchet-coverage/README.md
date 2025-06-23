# Ratchet coverage

Generate code coverage using `cargo tarpaulin` and fail the workflow if the
coverage percentage falls below a stored baseline.

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| baseline-file | File used to persist the baseline coverage percentage between runs | no | `.coverage-baseline` |
| args | Additional arguments passed to `cargo tarpaulin` | no | `''` |

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

`cargo tarpaulin` only runs on Linux hosts, so this action executes the
coverage check on `ubuntu-latest` runners and prints a message on other
platforms. When running on Windows, `bc` is installed via MSYS2 so the
float comparison works the same everywhere.

### How it works

The action restores the previous coverage baseline using
[actions/cache](https://github.com/actions/cache) and installs
`cargo-tarpaulin` if necessary. After running the coverage command, it compares
the new percentage with the stored baseline. The job fails if coverage drops.
On success, the baseline file is updated and saved back to the cache for future
runs.

### Requirements

- Use `ubuntu-latest` for the coverage run because `cargo tarpaulin` only supports Linux. On other platforms the step is skipped.

Release history is available in [CHANGELOG](CHANGELOG.md).
