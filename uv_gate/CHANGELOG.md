# Changelog

All notable changes to `uv_gate` will be documented in this file.

## Unreleased

## 0.2.1

- Add the `missing-git-source` failure class: uv says "Remote Git fetches are
  not allowed because network connectivity is disabled (i.e., with
  `--offline`)" when a tool or project depends on a Git source that is not
  cached yet. `prepare` and `tool` make their one online attempt for it; `run`
  never goes online. Only that sentence matches: the bare "Git operation
  failed" from an older uv stays `unknown`, because a failed fetch is not fixed
  by a retry. Recorded from the uv that setup-uv installed (0.13.0).
- Accept a PEP 508 direct reference at a full commit,
  `name[extra] @ git+URL@<40 hex digits>`, as a pin; a branch, a tag, a short
  SHA or a non-Git URL in that form is still refused.

## 0.2.0

- Add the `missing-python` failure class: uv reports "No interpreter found for
  … in managed installations" with the hint "A managed Python download is
  available for …, but uv is set to offline mode" when an interpreter such as
  PyPy has not been downloaded yet. Both lines are required, so the header
  alone stays `unknown`. `prepare` and `tool` make their one online attempt for
  it, which downloads the interpreter; `run` still never goes online. Add
  `--version` and `VERSION`. Found in the cmd-mox pilot, whose CI runner had no
  managed PyPy.

## 0.1.0

- Add `uv_gate.py`: a stdlib-only helper with `prepare`, `run` and `tool`
  commands implementing the uv robust-execution procedure (cleaned environment,
  global cache, copy mode across filesystems, offline gates, one bounded online
  step after a `cache-miss` or `offline-resolution` failure, no other retries).
- Refuse the whole `--refresh*`, `--upgrade*` and `--reinstall*` families and
  the `-U`, `-P` and `-n` aliases, alone or inside a short-flag cluster such as
  `-qU`; strip inherited `UV_OFFLINE`, `UV_NO_CACHE`, `UV_FROZEN`, `UV_LOCKED`
  and `UV_REFRESH*` and `UV_UPGRADE*`; turn a failed path resolution, device
  comparison, Git-shim setup or stalled cache query into a refusal, with
  `needs_copy_mode` a pure comparison; keep tool specifications out of logs;
  end each command with a `uv-gate: metric` line.
