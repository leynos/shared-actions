# Changelog

All notable changes to `uv_gate` will be documented in this file.

## Unreleased

- Add `uv_gate.py`: a stdlib-only helper with `prepare`, `run` and `tool`
  commands implementing the uv robust-execution procedure (cleaned environment,
  global cache, copy mode across filesystems, offline gates, one bounded online
  step after a `cache-miss` or `offline-resolution` failure, no other retries).
- Refuse the whole `--refresh*`, `--upgrade*` and `--reinstall*` families and
  the `-U`, `-P` and `-n` aliases, alone or inside a short-flag cluster such as
  `-qU`; strip inherited `UV_OFFLINE`, `UV_NO_CACHE`, `UV_FROZEN`, `UV_LOCKED`
  and `UV_REFRESH*` and `UV_UPGRADE*`; turn a failed device comparison into a
  refusal; keep tool specifications out of logs; end each command with a
  `uv-gate: metric` line.
