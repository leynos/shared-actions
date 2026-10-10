# Migrating to the scoped lane-upload rule in `cv005-contracts` 0.2.0

This guide covers the next release of `packages/cv005-contracts`. It changes
which `actions/upload-artifact` steps the `coverage.lane-hardening` clause
judges. Read it before bumping `CV005_CONTRACTS_REF` past the commit that
introduced the clause.

## What changed, and why

The clause used to judge every upload step in the pull-request closure against
every lane's coverage report, wherever the step ran. A report lives in one
job's workspace, so a step on another runner cannot select it, and the rule
refused benchmark, release and diagnostic uploads in unrelated jobs.

The clause now judges an upload only where the report can be:

- the lane job's own steps;
- the local composite actions that job runs, directly or through other local
  actions;
- jobs that wait for the lane job (`needs`, transitively) on a runner that may
  persist: a `runs-on` of `self-hosted`, a runner-group mapping, or an
  expression that could resolve to either, together with the local actions
  those jobs run.

Each of those places is judged against every report the lane writes. A path
under `${{ runner.temp }}/` is cleared when the rest is literal text with no
`..` component and no further expression, because that directory lies outside
the workspace. A lane job may also carry `github.event_name != '<event>'` (for
example `'schedule'`) when its coverage step carries the pull-request guard.

## What to do

- A repository whose lane uploads were already narrow needs nothing beyond the
  pin bump.
- A repository that was refused only for uploads in unrelated jobs or
  workflows (benchmarks, releases, wheels) can bump the pin and drop any
  workaround.
- A later job on a self-hosted runner or runner group that waits for the lane
  and uploads `.`, a parent directory or a glob over the workspace now fails.
  Name the files or subdirectories, write them under `${{ runner.temp }}/`, or
  stop the job waiting for the lane.
