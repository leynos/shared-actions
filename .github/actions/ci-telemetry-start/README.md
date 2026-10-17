# ci-telemetry-start

Starts a background sampler that records CPU, memory and free disk every ten
seconds, so that [`ci-telemetry-finish`](../ci-telemetry-finish) can print one
telemetry record when the job ends. It generalizes axinite's
`scripts/ci-resource-sampler.sh` and adds the aggregate `/proc/stat` CPU line.

## Usage

Call it early in a job, after `actions/checkout`, then call
`ci-telemetry-finish` last under `if: always()`; see the
[finish action](../ci-telemetry-finish) for the full example.

```yaml
- uses: actions/checkout@<sha>
- uses: leynos/shared-actions/.github/actions/ci-telemetry-start@<sha>
  with:
    interval-seconds: '10'
```

## Inputs

| Name             | Type   | Required? | Description                                                     | Default |
| ---------------- | ------ | --------- | --------------------------------------------------------------- | ------- |
| interval-seconds | string | no        | Seconds between samples; values below 1 are raised to 1.        | `10`    |
| disk-path        | string | no        | A directory on the volume whose minimum free space is recorded. | `.`     |

## Outputs

None.

## Behaviour

- It records the machine's shape (`nproc` as the process may use it, and the
  kernel's `CLK_TCK`) in `$RUNNER_TEMP/ci-telemetry/meta.json`, takes a first
  sample, and detaches a loop that appends one raw sample line per interval to
  `samples.tsv` in the same directory. It computes nothing; the finish action
  reduces the samples.
- On Windows and macOS it records the shape and starts no sampler. In a
  container job (`/.dockerenv`, or a container runtime in the init cgroup) it
  samples, but the finish action reports no CPU or memory, since `/proc` shows
  the host.
- It uses only the Python standard library and never fails a job: with no
  Python on `PATH` it warns and exits 0.
- The sampler wakes every interval and reads three small files, which is
  negligible for a compile job and unacceptable for one whose output is a
  timing. Do not run it on a benchmark job.

## Release history

See [CHANGELOG.md](CHANGELOG.md).
