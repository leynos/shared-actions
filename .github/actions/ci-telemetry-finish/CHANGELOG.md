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
- Add the test phase's sccache figures (`sccache_test_hits`, `_misses`,
  `_compile_requests`, `_hit_ratio`, `_problem`) as the difference between a
  post-build `sccache-report` snapshot and the final statistics, and
  `threshold_breaches`. New inputs `sccache-build-stats-file`,
  `test-hit-ratio-min`, `test-min-compile-requests`, `min-free-disk-mib` and
  `thresholds`. A breach on a push to the default branch prints a `::warning::`
  and never fails the job. The fields are additive to schema 1 and null when no
  snapshot is supplied.
