# Ratchet coverage

Generate code coverage using `cargo llvm-cov` and fail the workflow if the
coverage percentage falls below a stored baseline.

## Inputs

| Name          | Description                                                        | Required | Default              |
| ------------- | ------------------------------------------------------------------ | -------- | -------------------- |
| baseline-file | File used to persist the baseline coverage percentage between runs | no       | `.coverage-baseline` |
| args          | Additional arguments passed to `cargo llvm-cov`                    | no       | `''`                 |

## Outputs

| Name    | Description                                      |
| ------- | ------------------------------------------------ |
| percent | Coverage percentage reported by `cargo llvm-cov` |

## Example

```yaml
- uses: ./.github/actions/setup-rust@v1
- uses: ./.github/actions/ratchet-coverage@v1
  with:
    baseline-file: .cache/coverage-baseline
    args: --workspace
```

On Windows runners `bc` is installed via MSYS2, so the float comparison works
the same across platforms.

### How it works

The action restores the previous coverage baseline using
[actions/cache](https://github.com/actions/cache) and installs `cargo-llvm-cov`
if necessary. After running the coverage command, it compares the new
percentage with the stored baseline. Both values are rounded to two decimals
before comparison to avoid failures from floating‑point noise. The job fails if
coverage drops. On success, the baseline file is updated and saved back to the
cache for future runs.

## Caching

Two caches are used: one for the baseline file and another for cargo artefacts
and the `cargo-llvm-cov` binary. The baseline cache is restored when the action
starts and saved again after updating the file. The cargo cache uses the
operating system and the checksum of `Cargo.lock` to avoid rebuilds.

### Requirements

- The Rust toolchain must already be installed (for example via the
  [setup-rust](../setup-rust) action).
- Windows runners automatically install `bc` using MSYS2.

Release history is available in [CHANGELOG](CHANGELOG.md).
