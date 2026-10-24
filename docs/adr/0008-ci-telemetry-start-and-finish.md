# ADR 0008: CI telemetry is a start action, a finish action and a file

**Status:** Accepted **Date:** 2026-10-17

## Context

Runner right-sizing needs, per job, the cores a job used against the cores it
had, its peak memory and its smallest free disk. Today nothing measures CPU:
axinite's `scripts/ci-resource-sampler.sh` samples memory and disk only, and
cuprum's takes a label-derived vCPU count. The CI observability plan
(leynos/grafana `docs/plan.md`, step 2.1) asks for one shared primitive that
prints a single record at the end of every job, for a central collector to read
from the job log.

## Decision

- **Two actions and a file.** `ci-telemetry-start` appends raw samples to
  `$RUNNER_TEMP/ci-telemetry/samples.tsv` and records the machine's shape in
  `meta.json`. `ci-telemetry-finish` reduces the samples and prints the record.
  The sampler computes nothing, so the arithmetic is a pure function over a
  file that tests feed recorded `/proc/stat` data and compare to exact figures.
- **One directory per action.** The repository ships each action alone, so the
  two do not import from each other. They share the sample file's columns, and
  a test in each holds its copy to the other's.
- **Linux accounting.** Busy is `user + nice + system + irq + softirq`. Steal is
  not busy; it is reported as a fraction of all CPU time. `guest` is not added,
  since Linux counts it inside `user`. `nproc` is what the process may use, not
  derived from a runner label.
- **Honest degradation.** On Windows and macOS, in a container job (where
  `/proc` shows the host) and when the environment could not be probed, CPU is
  null and the record says why in `sampler`. Telemetry never fails a job.
- **A built marker.** The finish script builds `DF12_CI_TELEMETRY` from two
  strings, so a step command or script source never carries it and the log has
  one match per job.
- **Calibration is dispatch-only.** `calibrate-ci-telemetry.yml` runs the
  actions under a known load on each shape the estate right-sizes between. It
  spends paid minutes, so the placement contracts allow it exactly the four
  shapes it compares and require its workflow to be dispatch-only.

## Consequences

- The collector joins a record to the Jobs API by `run_id`, `attempt` and
  `runner_name`; `GITHUB_JOB` alone is shared by matrix legs.
- A caller must run the finish action under `if: always()` to record a failed
  or cancelled job. A time-out or a lost runner still leaves no record.
- Changing the sample columns is a breaking change to both actions and to the
  tests that pin them.

## Addendum, 2026-10-22: test-phase figures and thresholds

The record gained the test phase's sccache hit rate and advisory thresholds
(DF12-109). Decisions, without changing the accepted text above:

- The test phase is the final sccache statistics minus a snapshot taken by a
  second `sccache-report` call after the build. sccache's counters are
  cumulative, so subtraction is exact, and no change to `setup-rust` or
  `sccache-report` code is needed.
- The new fields are additive to schema 1 and null when they cannot be
  established, so the central collector's reader, which validates only its
  named fields, is unaffected.
- Thresholds are inputs with defaults taken from measured records (warm Linux
  jobs: whole-job hit ratios of 0.998 to 1.0; smallest healthy free disk about
  5.4 GiB; the disk failure that motivated the threshold left about 2 GiB). A
  breach warns and is recorded; it never fails a job, and by default applies
  only to pushes to the default branch.
- The hit ratio is judged on the exact quotient of the counters, not the rounded
  figure recorded.
