# uv-gate

Run `uv` reliably across repositories: a cleaned environment, the global cache,
offline gates, and at most one bounded online step.

`uv_gate.py` is a single standard-library file (Python 3.9 or later). It is not
a GitHub Action: repositories **vendor it byte-for-byte** as
`scripts/uv_gate.py`, so no gate depends on the network that may be failing. It
implements the user's procedure in `uv-robust-execution.md`.

## Usage

```make
UV_GATE ?= python3 scripts/uv_gate.py

prepare: ## Sync the locked environment, offline first
	$(UV_GATE) prepare --group dev

lint: prepare
	$(UV_GATE) tool --from 'ruff==0.16.4' -- ruff check .

test: prepare
	$(UV_GATE) run --group dev -- pytest -q
```

| Command                                         | Runs                                   | Network                                                                           |
| ----------------------------------------------- | -------------------------------------- | --------------------------------------------------------------------------------- |
| `prepare [UV_SYNC_ARGS...]`                     | `uv sync --locked --offline`           | One `uv sync --locked`, only after a `cache-miss` or `offline-resolution` failure |
| `run [UV_RUN_ARGS...] -- COMMAND...`            | `uv run --frozen --offline COMMAND...` | Never                                                                             |
| `tool [--from SPEC] [ARGS...] -- EXECUTABLE...` | `uv tool run --offline ...`            | One warming run, only after one of those two failures                             |

Pass the same groups, extras and Python version to `prepare` and to every `run`.
`tool` refuses a spec that is not pinned: use `name==VERSION`, `name@VERSION`
or `git+URL@<full 40-character commit SHA>`.

Set `UV_GATE_ALLOW_ONLINE=0` to forbid the online step entirely.

## What every command does first

1. Validates the request without touching the environment or filesystem:
   forbidden flags, a missing command and an unpinned tool are refused with
   status 2 before anything else runs.
2. Builds a cleaned environment: drops `GIT_CONFIG_*`, `GH_TOKEN`,
   `GITHUB_TOKEN`, `BASH_ENV`, `UV_CACHE_DIR`, `UV_TOOL_DIR`, `UV_OFFLINE`,
   `UV_NO_CACHE`, `UV_FROZEN`, `UV_LOCKED` and `UV_REFRESH*` and `UV_UPGRADE*`
   (inherited uv switches that would override the gate's own policy), removes
   `~/.lody` and its children from `PATH`, sets `GIT_TERMINAL_PROMPT=0`, and
   puts a one-file shim directory first on `PATH` so `git` is `/usr/bin/git`.
3. Finds `uv` on that cleaned `PATH`.
4. Asks `uv --no-config cache dir` for the global cache, creates it, and sets
   `UV_CACHE_DIR` to it. There is no per-repository `.uv-cache`.
5. Compares the cache's device with the project's virtual environment
   (`UV_PROJECT_ENVIRONMENT` or `.venv`, symlinks resolved; the repository when
   it does not exist yet) and sets `UV_LINK_MODE=copy` when they differ.

## Failure classes

uv's own output is passed through unchanged. After it, the helper prints one
line, `uv-gate: <class>: ...`. The exit status is uv's own; the helper's own
refusals exit with status 2.

| Class                | Meaning                                                                                 | Retried?                                                              |
| -------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `cache-miss`         | A required file is not in the cache (offline)                                           | `prepare` and `tool`: one online run                                  |
| `offline-resolution` | Offline resolution needs uncached data; a stale lock looks the same offline             | `prepare` and `tool`: one online run, which then names the real cause |
| `stale-lock`         | `uv.lock` is out of date                                                                | No. Run `uv lock` yourself and commit it                              |
| `missing-lock`       | No `uv.lock`                                                                            | No                                                                    |
| `missing-package`    | A package or version is not in the registry                                             | No                                                                    |
| `missing-revision`   | A pinned Git revision does not exist                                                    | No                                                                    |
| `auth`               | Authentication failed, or the repository is not accessible with the current credentials | No                                                                    |
| (unrecognized)       | Anything else                                                                           | No                                                                    |

Helper refusals (status 2): `uv` absent from the cleaned `PATH`, a cache
directory that cannot be created, an unpinned tool, a forbidden flag
(`--refresh*`, `--upgrade*` and `--reinstall*` as whole families, so
`--refresh-package` is refused too; the short aliases `-U`, `-P` and `-n`,
alone or inside a cluster such as `-qU` (a value glued to a short option, such
as `-pPyPy`, is refused too; write `-p PyPy`); `--no-cache`, `--no-offline`,
`--locked`, `--frozen`, `--offline`), a failed comparison of the cache and
project filesystems, a missing command, and usage errors.

Logs never carry a tool specification: a refusal or a warming notice names the
package only (or "a git+ URL"), because a Git URL can hold credentials. Each
command ends with one bounded line,
`uv-gate: metric uv-gate.<command>=<outcome>`, where the outcome is `ok`,
`failed` (uv's own failure) or `refused`.

The helper never runs `uv lock`, never purges or refreshes a cache, and never
retries a failed test.

## Vendoring

Copy `uv_gate.py` to `scripts/uv_gate.py` without editing it, then record the
digest the concordat rule will compare:

```bash
sha256sum uv_gate/uv_gate.py
```

The classification patterns were recorded from uv 0.9.21; the fixtures under
`tests/fixtures/` hold the real output they match.

## Tests

```bash
make test
```

`tests/test_uv_gate_unit.py` covers classification, pin checking, environment
cleaning and the device check. `tests/test_blackbox_gate.py` runs the helper
against a scripted fake `uv` (POSIX only) and asserts the exact call list for
each failure class, including that no retry happens outside the one bounded
online step.

## Release history

See [CHANGELOG](CHANGELOG.md).
