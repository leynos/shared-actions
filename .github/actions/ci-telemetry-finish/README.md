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
      - uses: actions/checkout@<sha>
      - uses: leynos/shared-actions/.github/actions/ci-telemetry-start@<sha>
      - run: make test
      - uses: leynos/shared-actions/.github/actions/ci-telemetry-finish@<sha>
        if: always()
```

With sccache, wire the two `setup-rust` and `sccache-report` outputs in:

```yaml
- id: setup-rust
  uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
- run: cargo test
- id: sccache
  uses: leynos/shared-actions/.github/actions/sccache-report@<sha>
  if: always()
  with:
    status: ${{ steps.setup-rust.outputs.sccache-status }}
- uses: leynos/shared-actions/.github/actions/ci-telemetry-finish@<sha>
  if: always()
  with:
    sccache-status: ${{ steps.setup-rust.outputs.sccache-status }}
```

Call finish last and under `if: always()`: a failed or cancelled job is the one
whose resource use you most want to know. Only a time-out or a lost runner
leaves no record.

## Inputs

| Name               | Type   | Required? | Description                                                                                               | Default              |
| ------------------ | ------ | --------- | --------------------------------------------------------------------------------------------------------- | -------------------- |
| sccache-status     | string | no        | The `sccache-status` output of `setup-rust` (`started` or `fallback`). When empty the status is inferred. | `''`                 |
| sccache-stats-file | string | no        | The JSON file `sccache-report` wrote. Read when it exists; otherwise finish asks sccache once.            | `sccache-stats.json` |

## Outputs

| Name        | Description                                                                                                                                                 |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| record-path | Path of the record's JSON, without the marker.                                                                                                              |
| sampler     | `proc-stat`, `unsupported-os`, `container`, `unknown-environment`, `not-started`, `state-invalid` or `error`. CPU fields are null unless it is `proc-stat`. |

## The record

One line, schema 1, event `ci_runner_telemetry`. The collector joins it to the
Jobs API metadata (runner label, timings, conclusion) and prices it. Nothing is
derived from a runner label here: the record carries the `nproc` the machine
actually presented.

| Field                                                                  | Meaning                                                                                                                        |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `schema`, `event`                                                      | `1` and `ci_runner_telemetry`.                                                                                                 |
| `sampler`                                                              | `proc-stat`, `unsupported-os` (Windows, macOS), `container`, `unknown-environment`, `not-started`, `state-invalid` or `error`. |
| `repository`, `workflow`, `job`, `run_id`, `attempt`, `sha`            | From the `GITHUB_*` variables. `job` is the job id; matrix legs share it.                                                      |
| `runner_name`, `runner_os`, `runner_arch`                              | From `RUNNER_*`. `runner_name` is unique per runner, so the collector can join matrix legs.                                    |
| `nproc`, `clk_tck`, `interval_seconds`, `sample_count`, `wall_seconds` | The machine's shape and the sampling span (first to last sample).                                                              |
| `cpu_busy_cores_avg`, `_p95`, `_max`                                   | Busy cores: utilization of an interval times `nproc`. Average is time-weighted; p95 is nearest-rank over intervals.            |
| `cpu_core_seconds`                                                     | Busy jiffies divided by `clk_tck`.                                                                                             |
| `cpu_efficiency`                                                       | `cpu_core_seconds / (wall_seconds * nproc)`.                                                                                   |
| `cpu_saturation_fraction`                                              | Fraction of intervals with utilization above 90%.                                                                              |
| `cpu_steal_fraction`                                                   | Steal as a fraction of all CPU time: time the job wanted and the hypervisor withheld.                                          |
| `peak_memory_mib`, `mem_total_mib`, `min_free_disk_mib`                | Peak (total minus available) memory, and the minimum free space on the watched volume.                                         |

### sccache fields

Every record carries these fields; they are null unless `sccache_status` is
`started`.

| Field                                                             | Meaning                                                                                                                                                                                                                                  |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sccache_status`                                                  | `started`, `fallback` or `absent`. From `sccache-status` when given; otherwise inferred.                                                                                                                                                 |
| `sccache_stats_source`, `sccache_stats_problem`                   | `file` (the `sccache-report` file), `sccache` (finish ran `--show-stats`) or `unreadable`, with the problem: `file-invalid`, `file-unreadable`, `file-shape`, `sccache-failed`, `sccache-timeout`, `sccache-invalid` or `sccache-shape`. |
| `sccache_version`, `sccache_cache_location_kind`                  | The release, and the backend kind: `local`, `ghac`, `s3`, `gcs`, `azure`, `redis`, and so on.                                                                                                                                            |
| `sccache_compile_requests`                                        | All compile requests sccache saw, cacheable or not.                                                                                                                                                                                      |
| `sccache_hits`, `sccache_misses`                                  | Sums of the per-language `cache_hits` and `cache_misses` counts.                                                                                                                                                                         |
| `sccache_hit_ratio`                                               | Hits over hits plus misses; null with no hits or misses.                                                                                                                                                                                 |
| `sccache_cacheable_ratio`                                         | Hits plus misses over compile requests; null with no requests.                                                                                                                                                                           |
| `sccache_read_errors`, `sccache_write_errors`, `sccache_timeouts` | Backend errors and timeouts.                                                                                                                                                                                                             |
| `sccache_non_cacheable`, `sccache_requests_not_cacheable`         | sccache's `non_cacheable_compilations` and `requests_not_cacheable`. The second is the one that moves.                                                                                                                                   |
| `sccache_raw`                                                     | The statistics as printed, only when under 2 KB of compact JSON.                                                                                                                                                                         |

The status is inferred as `absent` with no `sccache` binary, `started` when a
server accepts connections (on `SCCACHE_SERVER_UDS` if set, else on
`SCCACHE_SERVER_PORT`, default 4226) and `fallback` otherwise; `RUSTC_WRAPPER`
is not consulted, since sccache also serves C and C++ compilers as a launcher.
A server that never started prints empty default statistics, which look like an
idle cache; a `fallback` job therefore carries null counts and ratios, never
zeros. Finish asks sccache only when the report file is absent; one that exists
but cannot be read or parsed is a problem to report, not a reason to ask again.
Statistics that lack a field sccache has printed since 0.12, or give it the
wrong type, are `unreadable`, not zero.

How CPU time is counted, from `/proc/stat` deltas between samples:

- busy is `user + nice + system + irq + softirq`;
- `steal` is not busy and is reported on its own;
- `guest` and `guest_nice` are not added, because Linux already counts them
  inside `user` and `nice`;
- total is busy plus `idle`, `iowait` and `steal`.

An interval whose counters did not advance, or went backwards, is skipped.

The degraded states are visible in `sampler` rather than hidden:
`unknown-environment` (the start action could not read the files that tell a
container from a VM, so it does not claim the host's counters; disk is
reported), `not-started` (no start action ran), `state-invalid` (its state
exists but cannot be read) and `error` (finish itself failed; the line carries
only identity and the error's type name).

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

## Release history

See [CHANGELOG.md](CHANGELOG.md).
