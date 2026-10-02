# sccache-report

Prints sccache's statistics after a build, writes them as text and JSON, adds
them to the job summary, and stands down when [`setup-rust`](../setup-rust)
reports that the sccache server fell back to an uncached build.

## Why it exists

With `setup-rust` at shared-actions #546 a server that will not start within
its 60 s timeout no longer fails the job: the action clears `RUSTC_WRAPPER`,
raises a `sccache-fallback` annotation and sets its `sccache-status` output to
`fallback`. A server that never started has no statistics: with no server,
`sccache --show-stats` does not start one (`sccache --zero-stats` does) and
prints empty default statistics, a table of zeros for a job that never used the
cache, which reads as a wrapper that never reached the compiler. Every consumer
that read statistics after the build carried the same guard by hand. This
action owns it once.

It cannot live inside `setup-rust`: the statistics exist only after the build,
when `setup-rust` has long finished, and a composite action has no post step.

## Usage

```yaml
- uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
  id: setup-rust
- run: cargo build
- id: sccache
  if: always()
  uses: leynos/shared-actions/.github/actions/sccache-report@<sha>
  with:
    status: ${{ steps.setup-rust.outputs.sccache-status }}
    backend: ${{ steps.setup-rust.outputs.cache-backend }}
- name: Check sccache health
  if: steps.sccache.outputs.reported == 'true'
  env:
    STATS_FILE: ${{ steps.sccache.outputs.stats-file }}
  run: python3 scripts/check_sccache_health.py "$STATS_FILE"
```

## Inputs

All inputs are strings and all are optional.

| Name       | Type   | Required? | Description                                                                                          | Default              |
| ---------- | ------ | --------- | ---------------------------------------------------------------------------------------------------- | -------------------- |
| status     | string | no        | The `sccache-status` output of setup-rust. `fallback` stands the action down; anything else reports. | `''`                 |
| backend    | string | no        | The `cache-backend` output of setup-rust, named in the summary.                                      | `''`                 |
| stats-file | string | no        | Path the JSON statistics are written to. Must not contain a line break.                              | `sccache-stats.json` |
| text-file  | string | no        | Path the human-readable statistics are written to. Must not contain a line break.                    | `sccache-stats.txt`  |
| summary    | string | no        | `true` appends the statistics to the job summary.                                                    | `true`               |

## Outputs

| Name       | Description                                                                                                    |
| ---------- | -------------------------------------------------------------------------------------------------------------- |
| reported   | `true` when statistics were written, `false` when the action stood down (a fallback, or no sccache on `PATH`). |
| stats-file | The JSON path when `reported` is `true`, else empty.                                                           |

A health check that reads the JSON conditions on `reported`, so the guard lives
here and not in each consumer. Standing down is reported as a notice titled
`sccache-report` and as
`metric sccache-report.outcome=<reported|fallback|not-installed>`.
