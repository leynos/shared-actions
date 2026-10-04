# Migrating to `sccache-report`

This guide covers the new `sccache-report` action, added in the next release.
It replaces the `sccache --show-stats` step that each consumer of `setup-rust`
carried by hand, and it is optional: nothing changes for a repository that
keeps its own step.

## What changed, and why

Since `setup-rust` fails open (shared-actions #546), a server that will not
start within its 60 s timeout no longer fails the job. It reports
`sccache-status` as `fallback` and the job compiles uncached. A server that
never started has no statistics: with no server, `sccache --show-stats` prints
empty defaults rather than starting one, so an unguarded report publishes a
table of zeros for a job that never used the cache, and a health check run over
it reads as a broken integration.

Every consumer therefore needed the same guard, written by hand.
`sccache-report` owns it once.

## When to migrate

Consider it when a workflow runs `sccache --show-stats` after a build, writes
the statistics to a file for a health check, or adds them to the job summary. A
workflow that only builds needs nothing.

## How to migrate

Call the action after the build, under `if: always()`, and give it the two
outputs `setup-rust` already publishes:

```yaml
- id: sccache
  if: always()
  uses: leynos/shared-actions/.github/actions/sccache-report@<sha>
  with:
    status: ${{ steps.setup-rust.outputs.sccache-status }}
    backend: ${{ steps.setup-rust.outputs.cache-backend }}
```

- `status` is `setup-rust`'s `sccache-status`. `fallback` stands the action
  down; any other value, including empty, reports.
- `backend` is `setup-rust`'s `cache-backend`, named in the job summary because
  `Cache location` reads `ghac` for Ubicloud's proxy and for GitHub's own
  service alike.
- `stats-file`, `text-file` and `summary` are optional: the paths the JSON and
  text statistics are written to (defaults `sccache-stats.json` and
  `sccache-stats.txt`), and whether to append to the job summary.

The action sets two outputs. `reported` is `"true"` when statistics were
written and `"false"` when it stood down (a fallback, or no `sccache` on
`PATH`). `stats-file` is the JSON path when `reported` is `"true"` and empty
otherwise.

Gate any health check on `reported`, so a fallback run stays green with the
`sccache-fallback` annotation as its evidence:

```yaml
- name: Check sccache health
  if: ${{ !cancelled() && steps.sccache.outputs.reported == 'true' }}
  env:
    STATS_FILE: ${{ steps.sccache.outputs.stats-file }}
  run: python3 scripts/check_sccache_health.py "$STATS_FILE"
```

Then delete the handwritten `sccache --show-stats` step and its own fallback
guard. Keep `if: always()` on the action: a failed build is when the numbers
are wanted.

## Rolling back

Restore the previous `sccache --show-stats` step, with its own guard on
`sccache-status`, and remove the action. Nothing outside the workflow was
written, so there is nothing to undo.
