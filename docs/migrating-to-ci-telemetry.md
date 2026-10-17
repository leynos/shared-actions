# Adding CI telemetry to a job

This guide covers the new `ci-telemetry-start` and `ci-telemetry-finish`
actions, added in the next release. They generalize axinite's
`scripts/ci-resource-sampler.sh` and add CPU. Using them is optional: nothing
changes for a job that does not.

## What changed, and why

Runner right-sizing needs the cores a job used against the cores it had, and
nothing measured CPU: axinite's sampler records memory and disk only, and
cuprum's takes a vCPU count from the runner label. The pair samples the
aggregate `/proc/stat` line and prints one `DF12_CI_TELEMETRY {json}` record at
the end of the job, for the central collector to read.

## When to migrate

Add the pair to a Linux job whose runner shape you may want to change, or to
replace a handwritten resource sampler. A job that is a timing benchmark should
not carry it: the sampler wakes every 10 seconds.

## How to migrate

```yaml
steps:
  - uses: actions/checkout@<sha>
  - uses: leynos/shared-actions/.github/actions/ci-telemetry-start@<sha>
  - run: make test
  - uses: leynos/shared-actions/.github/actions/ci-telemetry-finish@<sha>
    if: always()
```

- Put start after checkout, so a `disk-path` inside the workspace exists.
- Put finish last, with `if: always()`.
- Replace a handwritten sampler's start and report steps with the pair, and
  delete its script once nothing else calls it.
- Read the record from the job log (`DF12_CI_TELEMETRY`) or the step summary.

## Rolling back

Remove the two steps. Nothing outside the runner's temporary directory is
written.
