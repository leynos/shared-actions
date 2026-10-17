# Changelog

All notable changes to the `ci-telemetry-finish` action will be documented in
this file.

## Unreleased

- Add the action. It stops the sampler started by `ci-telemetry-start`, takes a
  final sample and prints exactly one `DF12_CI_TELEMETRY {json}` line with CPU
  (busy cores average, p95 and maximum, core-seconds, efficiency, saturation
  and steal fractions), peak memory and minimum free disk, plus a summary
  table. CPU fields are null for `unsupported-os` and `container` jobs.
- Add sccache fields to the record: status (`started`, `fallback`, `absent`),
  version, cache backend kind, compile requests, hits, misses, hit and
  cacheable ratios, read and write errors, timeouts and non-cacheable counts.
  The statistics come from the `sccache-report` file when present, otherwise
  from `sccache --show-stats`. New inputs `sccache-status` and
  `sccache-stats-file`.
