# Mutation testing reusable workflow (cargo-mutants)

Runs scheduled, informational mutation testing for Rust repositories
with [`cargo-mutants`](https://mutants.rs/). A change-detection guard
makes quiet days a cheap no-op; scoped runs mutate only recently
changed files; manual dispatch runs mutate everything, fanned out
across shards. Surviving mutants are reported in the job summary and as
artefacts. The workflow never gates pull requests.

## Behaviour

- **Scheduled runs** scan commits reachable from `base-ref` within
  `window-hours` (commit timestamps, so fresh CI clones work), bucket
  changed `*.rs` files into targets, and mutate only those files. When
  nothing relevant changed, the run writes a skip message to the job
  summary and finishes in seconds.
- **`workflow_dispatch` runs** bypass the guard and run a full,
  unscoped mutation of every target, fanning the root target out across
  `shard-count` matrix legs (`cargo mutants --shard k/N`). Size the
  shard count so each leg fits the `timeout-minutes` ceiling; as a data
  point, a crate producing ~1,800 mutants needs roughly 15 single-job
  hours, and 6 shards bring that to ~2.5–3 hours per leg.
- **Exit codes** follow the informational contract: cargo-mutants exit
  codes 2 (missed mutants) and 3 (timeouts) are treated as success —
  survivors are the deliverable — while 1 (usage error), 4 (baseline
  tests already failing), and 70 (internal error) fail the job.
- **Reports:** each matrix leg uploads its `mutants.out/` directory as
  a `mutation-report-<target>-<shard>` artefact; a final job merges all
  legs and posts per-target outcome counts plus a surviving-mutants
  table to the job summary.

## Required permissions

The caller job must grant:

```yaml
permissions:
  contents: read
  id-token: write # resolves the pinned workflow source via OIDC
```

## Usage

```yaml
name: Mutation testing

on:
  schedule:
    - cron: "30 4 * * *" # daily; guard makes quiet days a no-op
  workflow_dispatch:

# Serialize runs; informational runs queue rather than cancel.
concurrency:
  group: mutation-testing-${{ github.ref }}
  cancel-in-progress: false

jobs:
  mutation:
    permissions:
      contents: read
      id-token: write
    uses: leynos/shared-actions/.github/workflows/mutation-cargo.yml@<pinned-sha>
    with:
      # Workspace crates outside the root need separate invocations.
      extra-crate-dirs: "my_testkit"
      # Keep example/test scaffolding out of the survivors table.
      exclude-globs: "src/examples.rs,src/test_helpers.rs"
      # Run feature-gated tests against mutants too.
      extra-args: "--all-features"
```

## Inputs

| Input | Default | Purpose |
| ----- | ------- | ------- |
| `paths` | `src/,examples/,benches/` | Path prefixes belonging to the root target. |
| `extra-crate-dirs` | (empty) | Non-workspace crate directories mutated as separate targets. |
| `exclude-globs` | (empty) | Comma-separated `--exclude` globs. |
| `window-hours` | `25` | Detection window; keep one hour wider than the cadence. |
| `base-ref` | `origin/main` | Reference scanned for changes. |
| `timeout-multiplier` | `3` | Per-mutant timeout as a multiple of the baseline. |
| `timeout-minutes` | `300` | Per-job ceiling. |
| `shard-count` | `6` | Fan-out for full dispatch runs (scoped runs stay single-shard). |
| `cargo-mutants-version` | pinned | Tool version; the summary parser is validated against it. |
| `extra-args` | (empty) | Extra cargo-mutants arguments (shell-lexed), e.g. `--all-features`. |
| `setup-commands` | (empty) | Shell commands run before cargo-mutants in each mutants job (e.g. `sudo apt-get install -y mold` when the repo's `.cargo/config.toml` selects that linker). |

## Notes

- The `cargo-mutants-version` default is pinned because the
  `outcomes.json` format is documented as unstable and the summary
  parser must match it. Override only alongside a parser check.
- Crates whose test coverage lives in a sibling crate (testkit and
  companion crates listed in `extra-crate-dirs`) report mostly false
  survivors, because only the crate's own tests run against its
  mutants. Treat those tables as advisory.
- Some survivors are equivalent mutants (behaviour-preserving
  rewrites); triage before turning the table into test tasks.
- GitHub disables cron triggers after 60 days of repository
  inactivity; a quiet repository silently stops running, which is an
  acceptable failure mode for an informational workflow.
- Callers with PyO3 embedding crates (the `auto-initialize` pattern)
  are handled automatically: the workflow adds the uv-provisioned
  interpreter's `LIBDIR` to `LD_LIBRARY_PATH` before running
  cargo-mutants, so test binaries linked against `libpython` load it at
  run time. No `setup-commands` workaround is needed.

## Local validation

Guard-path integration tests run under [`act`] via the shared harness:

```sh
ACT_WORKFLOW_TESTS=1 make test
```

See `tests/workflows/test_mutation_workflows.py` and
`docs/local-validation-of-github-actions-with-act-and-pytest.md`. The
mutation-run path itself is covered by the `workflow_scripts` unit
tests; act cannot inject stub toolchains into a `workflow_call` job.

[`act`]: https://github.com/nektos/act
