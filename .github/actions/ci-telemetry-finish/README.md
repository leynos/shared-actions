# ci-telemetry-finish

Stops the sampler that [`ci-telemetry-start`](../ci-telemetry-start) began and
prints exactly one `DF12_CI_TELEMETRY {json}` line for the central collector,
plus a summary table in the log and the job summary. It is the second half of a
pair: the start action samples, this one reduces the samples to a record.

## Usage

```yaml
jobs:
  build:
    runs-on: ubicloud-standard-4
    steps:
      - uses: leynos/shared-actions/.github/actions/ci-telemetry-start@<sha>
      - uses: actions/checkout@<sha>
      - run: make test
      - uses: leynos/shared-actions/.github/actions/ci-telemetry-finish@<sha>
        if: always()
```

Call finish last and under `if: always()`: a failed or cancelled job is the one
whose resource use you most want to know. Only a time-out or a lost runner
leaves no record.

## Outputs

| Name        | Description                                                                                                |
| ----------- | ---------------------------------------------------------------------------------------------------------- |
| record-path | Path of the record's JSON, without the marker.                                                             |
| sampler     | `proc-stat`, `unsupported-os`, `container` or `not-started`. CPU fields are null unless it is `proc-stat`. |

## The record

One line, schema 1, event `ci_runner_telemetry`. The collector joins it to the
Jobs API metadata (runner label, timings, conclusion) and prices it. Nothing is
derived from a runner label here: the record carries the `nproc` the machine
actually presented.

| Field                                                                  | Meaning                                                                                                             |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `schema`, `event`                                                      | `1` and `ci_runner_telemetry`.                                                                                      |
| `sampler`                                                              | `proc-stat`, `unsupported-os` (Windows, macOS), `container` or `not-started`.                                       |
| `repository`, `workflow`, `job`, `run_id`, `attempt`, `sha`            | From the `GITHUB_*` variables. `job` is the job id; matrix legs share it.                                           |
| `runner_name`, `runner_os`, `runner_arch`                              | From `RUNNER_*`. `runner_name` is unique per runner, so the collector can join matrix legs.                         |
| `nproc`, `clk_tck`, `interval_seconds`, `sample_count`, `wall_seconds` | The machine's shape and the sampling span (first to last sample).                                                   |
| `cpu_busy_cores_avg`, `_p95`, `_max`                                   | Busy cores: utilization of an interval times `nproc`. Average is time-weighted; p95 is nearest-rank over intervals. |
| `cpu_core_seconds`                                                     | Busy jiffies divided by `clk_tck`.                                                                                  |
| `cpu_efficiency`                                                       | `cpu_core_seconds / (wall_seconds * nproc)`.                                                                        |
| `cpu_saturation_fraction`                                              | Fraction of intervals with utilization above 90%.                                                                   |
| `cpu_steal_fraction`                                                   | Steal as a fraction of all CPU time: time the job wanted and the hypervisor withheld.                               |
| `peak_memory_mib`, `mem_total_mib`, `min_free_disk_mib`                | Peak (total minus available) memory, and the minimum free space on the watched volume.                              |

How CPU time is counted, from `/proc/stat` deltas between samples:

- busy is `user + nice + system + irq + softirq`;
- `steal` is not busy and is reported on its own;
- `guest` and `guest_nice` are not added, because Linux already counts them
  inside `user` and `nice`;
- total is busy plus `idle`, `iowait` and `steal`.

An interval whose counters did not advance, or went backwards, is skipped.

On Windows and macOS (`unsupported-os`) every measurement is null. In a
container job (`container`) `/proc` shows the host, so CPU and memory are null
and only `min_free_disk_mib` is reported.

## Behaviour worth knowing

- Finish stops the sampler by PID and takes its own final sample, so the last
  interval is never lost. The runner's orphan-process cleanup runs after a
  job's steps, so the sampler may already be gone; the record is then built
  from the samples on disk.
- The marker is built in the script and never written in the manifest, so the
  runner's echo of a step command cannot carry a second match.
- The record is built from named `GITHUB_*` and `RUNNER_*` variables only. A
  secret whose value appeared in the JSON would be masked as `***` and corrupt
  the line, so no value comes from a secret.
- The action never fails a job. If Python is missing it warns and exits 0.
- A job that never ran the start action prints a record with `sampler`
  `not-started` and a warning.
