# Changelog

All notable changes to `uv_gate` will be documented in this file.

## Unreleased

- Add `uv_gate.py`: a stdlib-only helper with `prepare`, `run` and `tool`
  commands implementing the uv robust-execution procedure (cleaned environment,
  global cache, copy mode across filesystems, offline gates, one bounded online
  step for a proven cache miss, no other retries).
