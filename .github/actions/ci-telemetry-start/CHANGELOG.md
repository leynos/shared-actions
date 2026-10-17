# Changelog

All notable changes to the `ci-telemetry-start` action will be documented in
this file.

## Unreleased

- Add the action. It starts a detached sampler that appends raw `/proc/stat`,
  memory and free-disk samples every interval for `ci-telemetry-finish` to
  reduce, generalizing axinite's `ci-resource-sampler.sh`.
