# Ratchet coverage

Generate code coverage using `cargo llvm-cov` and fail the workflow if the
coverage percentage falls below a stored baseline.

## Inputs

| Name             | Description                                                        | Required | Default              |
| ---------------- | ------------------------------------------------------------------ | -------- | -------------------- |
| baseline-file    | File used to persist the baseline coverage percentage between runs | no       | `.coverage-baseline` |
| args             | Additional arguments passed to `cargo llvm-cov`                    | no       | `''`                 |
| publish-baseline | When the baseline may be published: `auto` or `always`             | no       | `auto`               |

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
coverage drops. On success the baseline file is updated, and on a push to
`refs/heads/main` it is saved back to the cache for future runs. A
`workflow_dispatch`, and a push to any other branch, update the file for the
run and publish nothing.

## Caching

Two caches are used: one for the baseline file and another for cargo artefacts
and the `cargo-llvm-cov` binary. The baseline cache is restored when the action
starts, and saved again only when the run is allowed to publish. The cargo
cache uses the operating system and the checksum of `Cargo.lock` to avoid
rebuilds.

## When the baseline is published

The save runs only on a `push` to `refs/heads/main`. A `workflow_dispatch`
never publishes, and neither does a push to any other branch.

A dispatch is how warm-cache evidence is gathered, by re-running a workflow
over an unchanged tree; a run that publishes disturbs the generation it was
measuring, and adds a cache entry rather than replacing one, because the key
names the run. A push to a branch other than the trunk would advance the
baseline that later pull requests are measured against, which is a correctness
question rather than housekeeping.

Set `publish-baseline: always` when a repository's merges fire no `push` event,
because they land through an automerge token, or when its trunk is not called
`main`. The calling workflow is then responsible for restricting the job to the
runs that should publish, since the action no longer is. Any value other than
`auto` or `always` is refused before the action does anything, rather than
being treated as `auto`: a typo would otherwise stop publication silently and
leave later runs comparing against a baseline that had stopped advancing.

### Requirements

- The Rust toolchain must already be installed (for example via the
  [setup-rust](../setup-rust) action).
- Windows runners automatically install `bc` using MSYS2.

Release history is available in [CHANGELOG](CHANGELOG.md).
