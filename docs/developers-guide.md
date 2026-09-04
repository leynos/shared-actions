# Developer's Guide

This document describes internal architecture and maintenance conventions for
the shared actions in this repository. It covers action-specific implementation
notes, public and internal APIs that affect contributors, concurrency
assumptions, and Makefile tool-resolution strategy.

## Spelling policy

Run `make spelling` to enforce en-GB-oxendict spelling. The dictionary-based
Typos scan checks tracked Markdown, while the phrase-correction check covers
the whole tracked repository, including Python, PowerShell, Rust and workflow
files. The generated and tracked `typos.toml` starts from the shared Oxford
dictionary. Its builder refreshes the untracked `.typos-oxendict-base.toml`
cache and metadata only when the shared dictionary is newer, so the last
fetched base remains usable in a network-restricted checkout.

Keep repository-specific identifiers and deliberate quotations in
`typos.local.toml`. Run `make spelling-config-write` to regenerate the tracked
configuration and `make spelling-config` to verify it. Never edit generated
entries by hand.

## Architecture Decision Records

- [ADR 0002: Explicit ps-module-name for PowerShell sidecars](adr/0002-explicit-ps-module-name.md)

## Python Coverage Venv Architecture

### Motivation

Earlier revisions of `run_python.py` invoked slipcover and coverage.py via
`uv run --with slipcover --with pytest --with coverage python ...`, which
re-resolved and reinstalled tooling on every invocation and could not cache the
interpreter reference within the same process.

### Lifecycle

`run_python.py` manages a dedicated throwaway virtual environment at
`.venv-coverage` in the working directory.

| Step | Function                    | Description                               |
| ---- | --------------------------- | ----------------------------------------- |
| 1    | `_find_coverage_python()`   | Locate the Python executable.             |
| 2    | `_remove_coverage_venv()`   | Remove the venv or placeholder path.      |
| 3    | `_recreate_coverage_venv()` | Recreate the venv.                        |
| 4    | `_ensure_coverage_venv()`   | Sync project deps and install tooling.    |
| 5    | `_coverage_python_cmd()`    | Return the cached `plumbum.BoundCommand`. |

`_find_coverage_python()` returns `None` when `.venv-coverage` is absent, is a
symlink, is a non-directory, or lacks a Python executable.
`_remove_coverage_venv()` uses `shutil.rmtree` for directories and
`Path.unlink` for files or symlinks. `_recreate_coverage_venv()` raises
`RuntimeError` if the executable is still absent after creation.
`_ensure_coverage_venv()` runs `uv sync --inexact --python <venv_python>` and
`uv pip install --python <venv_python> slipcover>=1.0.18 pytest pytest-xdist coverage`.
The `slipcover>=1.0.18` floor ensures the xdist plugin is present so
`pytest -n <workers>` runs merge per-worker coverage transparently; the
constraint also forces uv to upgrade any older slipcover installed earlier by
`uv sync`. `<venv_python>` is the absolute path inside `.venv-coverage`; it is
not resolved through symlinks before being passed to uv.
`_coverage_python_cmd()` uses `@lru_cache(maxsize=1)` and returns the cached
command for `<venv_python>` thereafter.

### Public API

<!-- markdownlint-disable MD013 -->
| Symbol                 | Signature                                                                     | Role                                                                        |
| ---------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `coverage_cmd_for_fmt` | `(fmt, out, workers="")`                                                      | Build a slipcover command, optionally with `-n <workers>` for pytest-xdist. |
| `tmp_coveragepy_xml`   | `(out)`                                                                       | Generate temporary Cobertura XML.                                           |
| `main`                 | `(output_path, lang, fmt, github_output, baseline_file, pytest_workers=None)` | Run.                                                                        |
<!-- markdownlint-enable MD013 -->

`coverage_cmd_for_fmt` returns a `BoundCommand` for the requested format. When
`workers` is non-empty, it appends `-n <workers>` so pytest-xdist parallelizes
the run; an empty string preserves the historical serial pytest invocation.
`tmp_coveragepy_xml` yields a temporary XML path and removes it on exit. `main`
resolves `pytest_workers` from the CLI option, falling back to the
`INPUT_PYTEST_WORKERS` environment variable and finally to `"auto"`. Accepted
values are `"auto"`, `"logical"`, a positive integer string, or `""` to disable
parallelism — `"0"` is rejected so that `""` stays the single canonical disable
mechanism. `main` then runs slipcover, parses coverage, and writes
`GITHUB_OUTPUT`.

### Concurrency Model

`run_python.py` runs as a single-threaded GitHub Actions step. The
`@lru_cache(maxsize=1)` on `_coverage_python_cmd()` therefore requires no
explicit synchronization; the cache is safe for the lifetime of the process.

### Broken-Venv Recovery

If `.venv-coverage` is present but its Python executable is absent (or a
non-directory placeholder occupies its path), `_recreate_coverage_venv()`
removes the directory and recreates it from scratch. The case is detected by
`_find_coverage_python()` returning `None` when the directory already exists.

### POSIX and Windows Layouts

`_find_coverage_python()` checks three candidate paths in order:

- `COVERAGE_VENV/bin/python` (POSIX)
- `COVERAGE_VENV/Scripts/python.exe` (Windows)
- `COVERAGE_VENV/Scripts/python` (Windows without extension)

On POSIX, `bin/python` is commonly a symlink to the base interpreter, for
example `/usr/bin/python3.12`. `_find_coverage_python()` deliberately returns
`Path.absolute()` rather than `Path.resolve()` so the action passes
`.venv-coverage/bin/python` to uv. Resolving that symlink would make
`uv pip install --python` target the externally managed system interpreter
instead of the throwaway coverage venv.

The helper logs each candidate at DEBUG level with the candidate path, absolute
path, resolved target, file status, and symlink status.
`_ensure_coverage_venv()` logs the candidate set and the exact Python path
passed to `uv sync` and `uv pip install` at INFO level so CI logs can identify
whether uv targeted the venv path or the base interpreter.

## Makefile Tool Resolution

The `Makefile` resolves optional local tool installations before falling back
to bare names on `PATH`.

| Variable           | Default resolution order                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `UV`               | `~/.local/bin/uv` if present, otherwise `uv`                                                                 |
| `ACT`              | `~/go/bin/act` if present, then `~/.local/bin/act` if present, otherwise `act`                               |
| `ACTION_VALIDATOR` | `~/.bun/bin/action-validator` if present, then `~/.cargo/bin/action-validator`, otherwise `action-validator` |
| `MDLINT`           | `~/.bun/bin/markdownlint-cli2` if present, otherwise `markdownlint-cli2`                                     |

Override example:

```bash
make lint UV=uv
make test ACT=/usr/local/bin/act
```

## `setup-uv` Pinning

Actions and workflows in this repository consume `astral-sh/setup-uv` by full
commit SHA rather than by mutable version tags. This follows the repository
security rule for third-party actions: callers should execute a reviewed Git
object, not whatever a tag happens to resolve to later.

Keep all `setup-uv` references on the same SHA unless there is a deliberate
compatibility reason to split them. A pin update is repository-wide
maintenance: search for `astral-sh/setup-uv@`, update every matching action or
workflow reference together, and run the normal action test gates before review.

The `macos-package` action passes `version: latest-known`, so it requires a
setup-uv revision that resolves that value from bundled checksum metadata. Its
current compatible pin is `20cfd1bf945f4377ade1205e4dbc17946fc9a30d`; this
avoids fetching Astral's mutable remote versions manifest during release
packaging. Keep the action's manifest test synchronized with both this SHA and
`latest-known`. The repository's `rust-toy-app.yml` workflow exercises the
actual action on macOS; local `act` tests cannot cover that path because macOS
packaging tools are not available in its Linux container runtime.

When changing the pin, include the target SHA in the change description and
verify affected act workflow tests where the action runs under `nektos/act`. If
act cannot execute the real `setup-uv` path on the local runner, document the
reason and keep the unit or manifest tests that assert the pinned reference in
sync with the new SHA.

## Maintaining `setup-rust` Node.js Action Pins

The [`setup-rust` action manifest](../.github/actions/setup-rust/action.yml)
uses `actions/cache`, `mozilla-actions/sccache-action`, and
`msys2/setup-msys2`. Pin each action by a verified full commit SHA.

When updating these Node.js 24 action dependencies:

1. Inspect the upstream `action.yml` at the selected revision and verify that
   its `runs.using` value declares the required Node.js runtime.
2. Update every affected `setup-rust` manifest step together so all supported
   runner paths use the intended revisions.
3. Synchronize the exact revision strings in `NODE24_ACTION_REVISIONS` in the
   [`setup-rust` manifest tests](../.github/actions/setup-rust/tests/test_setup_rust_manifest.py).
4. Run the manifest tests and supported runner-backed workflow validation.

The static manifest assertions must remain in place: runner execution proves
that the action works, but cannot prove that a pin is the intended revision.

## `setup-rust` and the rustc wrapper

`mozilla-actions/sccache-action` installs sccache and exports `SCCACHE_PATH`.
It does not export `RUSTC_WRAPPER`, and Cargo routes compilation through
sccache only when that variable names it. So `setup-rust` exports the wrapper
itself, in a step that must follow both sccache-action steps because
`SCCACHE_PATH` is their output.

The failure this fixes was silent, which is why it lasted: the action reported
sccache as enabled, the binary was installed, and every consumer that did not
set the wrapper itself compiled without it. Chutoro recorded zero compile
requests, and the cost only became visible once the `target` archive that had
been masking it was removed.

Rules to keep:

- A caller's existing `RUSTC_WRAPPER` wins, including a deliberate empty value,
  and the action says so in a notice. Someone wrapping rustc for their own
  reasons must not be overridden silently. The guard uses `${RUSTC_WRAPPER+x}`
  rather than `-v`, which needs Bash 4.2 that macOS runners do not have.
- A missing `SCCACHE_PATH` fails the step rather than skipping the export.
  Skipping would restore exactly the silent uncached build this exists to end.
- The wrapper is written to `GITHUB_ENV` **before** the counters are zeroed. A
  failure while zeroing costs a clean statistics baseline; writing second would
  cost the cache itself, so that path warns and keeps the wrapper.

Every terminal path reports one bounded
`metric setup-rust.sccache.wrapper=<state>` line over `exported`,
`exported-stats-not-zeroed`, `caller-set`, and `missing-sccache-path`. Keep the
name fixed and the values inside that set, with no path or wrapper value in the
line.

The backend is chosen in a separate step **before** the sccache-action steps,
and that position is the whole point. sccache binds its backend once, when the
server starts, and the first thing here to start one is the `--zero-stats` in
the wrapper step that follows those sccache steps. `GITHUB_ENV` reaches only
the next step, so `SCCACHE_GHA_ENABLED` exported alongside the wrapper would be
read by nobody: the job would keep a local-disk cache while every log line
claimed the backend was selected. The two exports therefore sit on opposite
sides of the sccache steps, each for its own reason, and neither can move. A
manifest test holds both orderings.

The sccache-action steps do not start a server themselves, which is worth
knowing before reasoning about this ordering. What they do do is write
`ACTIONS_CACHE_SERVICE_V2=on` to `GITHUB_ENV`, forcing GitHub's v2 cache
service on every step after them. On a GitHub-hosted runner that is what a
caller wants. On Ubicloud it overrides the cleared value that
`export-ubicloud-cache-credentials` published, and the proxy serves v1. That is
issue `#441`, not this ordering.

Without that variable sccache writes to local disk, which nothing persists, so
`RUSTC_WRAPPER` alone buys a wrapper and an empty cache.

The selection order matters and the tests hold it. An explicit
`SCCACHE_GHA_ENABLED` wins, `false` and empty included, and reports `caller`:
someone who turned the cache off did so deliberately, and a `SCCACHE_DIR` set
alongside it does not override that. Failing an explicit value, a caller-set
`SCCACHE_DIR` means they mounted storage of their own, and reports `local`.
Otherwise the GitHub Actions backend is chosen and reports `gha`.

`.github/workflows/test-setup-rust-sccache.yml` proves the part the unit tests
cannot: on a real runner it builds a trivial crate after the action and asserts
sccache recorded at least one compile request, which is exactly the measurement
that was zero before this change, and that its cache location reads `ghac`
rather than `Local disk`. Assert both: the request count alone passed while the
cache was still local, which is how that gap survived the first change. Further
jobs assert a caller's wrapper and a caller's `SCCACHE_GHA_ENABLED=false` both
survive.

On Ubicloud, `export-ubicloud-cache-credentials` must run **before**
`setup-rust`. The GitHub Actions backend reads its endpoint when the sccache
server starts, so credentials published afterwards arrive too late and the
compiler cache silently uses whatever the runner advertised.

## Rust action cache ownership

The [`setup-rust`](../.github/actions/setup-rust/action.yml) and
[`generate-coverage`](../.github/actions/generate-coverage/action.yml)
manifests share a `cache-provider` boundary. `github` is the
backward-compatible default: the actions own their Cargo archive caches, while
setup-uv retains its automatic GitHub-hosted versus self-hosted policy.
`external` disables those Cargo and uv archive caches so the caller can mount
the same paths through exactly one other provider. Consumers on Ubicloud, or on
any other caller-owned cache setup, continue to use `cache-provider: external`.
The coverage action deliberately leaves ratchet-baseline paths under their
separate GitHub cache because external mode does not mount them.

Those baseline steps use the `actions/cache/restore` and `actions/cache/save`
sub-actions rather than the full `actions/cache` action. The full action
registers a post-job save on its own primary key, so pairing it with an
explicit save step gave the same run-id-suffixed key two writers, and the
reservation lost the race on every run. Keep both halves on one pinned
revision; the manifest test in
[`test_ratchet_baseline.py`](../.github/actions/generate-coverage/tests/test_ratchet_baseline.py)
enforces both properties. A Hypothesis property in the same file singles the
pairing out among every combination of the three cache action variants: the
earlier step must read and not write, and the later step must write and not
read. Any other pairing either gives the run-id key two writers or restores the
baseline again after the ratchet has advanced it.

Every action in this repository reaches `actions/cache`, and its `restore` and
`save` sub-actions, through one pinned commit. A moving tag such as `@v4` can
resolve to a release a transparent runner cache does not intercept, so its
saves become wasted upload. The contract in
[`test_cache_action_pinning.py`](../.github/actions/tests/test_cache_action_pinning.py)
sweeps every manifest under `.github/actions`, requires each reference to be a
full commit SHA, and requires all of them to share one revision, so bumping the
cache action stays a single decision rather than a per-action drift.

Two actions persist a ratchet baseline, `generate-coverage` and
`ratchet-coverage`, and both must use the same cache shape. The contract in
[`test_ratchet_baseline_cache.py`](../.github/actions/tests/test_ratchet_baseline_cache.py)
is parametrized over both: the restore/save split, one pinned revision per
pair, a run-scoped key shared by both halves, the prefix restore-key that
recovers the newest entry, restore before save over identical paths, and no
`cache-hit` guard on the save. Each action's own test directory keeps only what
is specific to it.

A separate reporting step emits bounded outcomes for both halves, because the
save runs long after the archive-cache reporter. Keep the restore states closed
to `hit`, `miss`, `skipped`, `disabled`, and `error`, and the save states to
`saved`, `skipped`, `disabled`, and `error`. Keep `skipped` distinct from
`disabled`: the steps carry an implicit `success()`, so an earlier failure
skips them while the cache is still configured, and conflating the two would
report a live cache as switched off. The notice must never carry the key, which
embeds the run id, nor the baseline paths.

The same states are emitted as two fixed metric lines,
`metric ratchet-cache.restore=<state>` and `metric ratchet-cache.save=<state>`,
for consumers that scrape rather than read. Keep the metric names fixed and the
values drawn from those vocabularies: a name or value that varied with the run
would give the series unbounded cardinality and make it useless to aggregate.
A test enumerates every step-outcome combination and asserts the emitted values
stay inside both sets, so widening a vocabulary in the manifest without
widening it there fails.

Those archive caches cover the Cargo registry and Git index only, plus the
installed Cargo binaries in the coverage action. Neither action archives the
`target` tree, and sccache owns compiler output in both. Repositories across
this estate build in two shapes: a debug or dev-fast tree built with Cranelift
and linked with mold for lint and test, and an instrumented
`target/llvm-cov-target` tree for coverage. A `target` archive captures one
shape, is invalidated on almost every change, and overlaps with sccache; the
rstest-bdd pilot measured 3.65 GB moved in about 121 s for one such archive.
sccache instead holds both shapes in a single store keyed by compiler flags.
Whitaker run 33744418209 (coverage under `-C instrument-coverage`) and Cuprum
run 33677926269 (Cranelift-built Whitaker lints) each report
`Non-cacheable compilations 0`, which is the evidence that the two shapes
coexist without conflict.

Size `SCCACHE_CACHE_SIZE` for both shapes. sccache defaults to a 10 GiB store.
Under the GitHub Actions backend (`SCCACHE_GHA_ENABLED=true`) GitHub's own
per-repository limit applies instead, so neither manifest exposes a sizing
input nor exports the variable. Callers that self-manage a local sccache
directory raise `SCCACHE_CACHE_SIZE` above the default so one store holds both
shapes.

Coverage keeps the LLVM codegen backend. Cranelift has no
`-C instrument-coverage` equivalent and cannot emit coverage instrumentation,
so an instrumented build must use LLVM. mold remains usable as the linker for
those builds.

[ADR 0003](adr/0003-sccache-owns-rust-compiler-output.md) records this decision
for both actions, including the measurements behind it and the consequence of a
cold sccache store.

Do not couple this input to `use-sccache`. The setup action's compiler-cache
backend is independent of its Cargo and uv archive caches. A caller mounting
`~/.cache/sccache` must disable the shared sccache action, install a trusted
prebuilt binary, set `RUSTC_WRAPPER=sccache`, and report the external volume's
cache result itself.

`rust-build-release` owns no caches itself. It declares the same
`cache-provider` and `use-sccache` inputs, validates the provider with the same
guard, and forwards both verbatim to its pinned nested `setup-rust` step, so a
caller three layers up can still name the cache owner. Its pin must therefore
stay on a revision that declares both inputs; the contract test in
[`test_setup_rust_reference.py`](../.github/actions/rust-build-release/tests/test_setup_rust_reference.py)
holds the expected SHA, the referenced revision's input surface, and the
forwarding together, so a bump that drops either input fails there.

Both actions validate the provider before cache use and report bounded provider
and archive-cache outcomes. Keep the allowed states closed to `hit`, `miss`,
`disabled`, and `error`; never include cache keys, paths, tokens, or raw errors
in the notice. The property tests in the setup-rust and generate-coverage test
directories prove that only the two exact provider names are accepted. Their
reporter tests exercise success, disabled, and failure observations. The
cross-action contract test in
[`.github/actions/tests/test_no_target_cache.py`](../.github/actions/tests/test_no_target_cache.py)
fails if a `target` path reappears in either manifest's cache inputs. When
this boundary changes, update both manifests, their action READMEs and
changelogs, the users' guide, that contract test, and these tests together.

## `install-whitaker` staging paths on Windows

`RUNNER_TEMP` is a native path, so under Git Bash the staging directory arrives
as `D:\a\_temp/whitaker-installer-release`. GNU tar reads a colon in an archive
path as rmt's `host:path` syntax, so it treated the drive letter as a hostname
and failed with `Cannot connect to D: resolve failed`, after the archive had
already been downloaded and verified.

Two defences, at different levels, and both are deliberate:

- The path is converted with `cygpath -u` in the resolve step, where it is
  first produced, so download, verify, extract, and install all receive the
  POSIX form. Converting in each consumer would leave the next one to remember.
  `cygpath` exists under Git Bash and nowhere else, hence the probe.
- GNU tar is passed `--force-local`, which covers a colon arriving from
  anywhere the conversion does not reach. bsdtar, the bundled tar on Windows
  and macOS runners, exits non-zero on that flag, so it is passed only after a
  version probe identifies GNU tar. The two cases are written as two `tar`
  calls rather than an options array, because macOS runners ship Bash 3.2,
  where expanding an empty array under `set -u` is an unbound-variable error.

Test the conversion by executing the resolve fragment, not by reading it. An
assertion on the fragment's text passes when `cygpath` runs without its result
being assigned, or when it runs after the resolve script, and in both cases
every later step still receives the native path. The tests in
[`test_install_whitaker_windows_paths.py`](../.github/actions/install-whitaker/tests/test_install_whitaker_windows_paths.py)
stub `cygpath` and the resolve script and assert on the value the script
received.

## `export-ubicloud-cache-credentials` action contract

A GitHub Actions runner exposes `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN`
to action steps only. A `run:` step never sees them. That is why this action's
single step is a pinned `actions/github-script` invocation rather than a shell
fragment: JavaScript running as an action can read the values, and
`core.exportVariable` republishes them through `GITHUB_ENV` for every later
step. Replacing that step with `run:` would export nothing and fail silently,
so the manifest test asserts the pinned reference.

On Ubicloud the cache URL names a proxy on the runner's private network, and
that proxy stores objects in Ubicloud's cache rather than GitHub's. The
evidence for the distinction is direct: cuprum run 33748907011 exported the
proxy URL, ran sccache 0.12 against the v1 service, and landed 167 objects in
Ubicloud's store, while the netsuke run for #664 and rstest-bdd run 33801703494
exported only `ACTIONS_RESULTS_URL`, ran sccache 0.16 against v2, and either
wrote to GitHub or failed outright.

Two consequences the action encodes:

- `ACTIONS_CACHE_SERVICE_V2` is exported empty. sccache's GitHub Actions
  backend selects the v2 service whenever that variable is set, and the proxy
  serves v1, so the runner's value has to be cleared rather than passed
  through.
- A public cache host fails the step. This is an Ubicloud-only action; on a
  GitHub-hosted runner the variable points at GitHub's endpoint, and exporting
  that under this action's name would send sccache somewhere other than where
  the job believes. Private means an RFC 1918 range, IPv4 loopback,
  `localhost`, or an IPv6 unique-local or loopback address. `localhost` is the
  one name accepted; every other host must be a complete address literal.
  Checking a prefix would accept the DNS name
  `10.attacker.example` and hand it the runtime token, so the address is parsed
  as a dotted quad, or as an IPv6 hextet for the unique-local range, before any
  range is considered.

The proxy URL's path segment is bearer-like, so the action masks the URL as
well as the token, and its single notice names only the host and port. Keep it
that way: a notice carrying the path would publish a credential to the log, and
register the secrets before anything is written, because the runner redacts
only what it already knows.

Every terminal path reports one bounded
`metric ubicloud-cache-credentials.result=<state>` line over a closed set:
`exported`, `missing-cache-url`, `missing-runtime-token`, `invalid-url`, and
`public-host`. Keep the name fixed and the values inside that set, and keep
tokens, URLs, hosts, and error text out of both.

The runner-backed workflow,
`.github/workflows/test-export-ubicloud-cache-credentials.yml`, asserts one
thing only: that the action refuses a GitHub-hosted runner. The success path
cannot be simulated there. The runner supplies its own `ACTIONS_CACHE_URL` and
`ACTIONS_RUNTIME_TOKEN` to every action step and **overrides workflow-level
values of the same names**, so a job that sets a private URL still sees
GitHub's public one inside the action. That was established by observation: an
earlier version of this workflow set a `10.0.0.0/8` URL at job level, the step
log showed it in the declared environment, and the action still read
`artifactcache.actions.githubusercontent.com`.

So the success path belongs to the Node tests, which run the shipped script
directly, and the refusal belongs to the runner, which is the only place a
real GitHub cache endpoint can be put in front of the guard. Do not try to
recover the success path by adding an input that overrides the environment:
that would put a way to bypass the private-host check into the action's public
surface.

Callers set `RUSTC_WRAPPER` and `SCCACHE_GHA_ENABLED` after this action and
before any step that starts an sccache server, because sccache reads the cache
configuration once at server start and keeps it for that server's life.

## `install-whitaker` action contract

The composite action's built-in cache restores
`${{ steps.validate-inputs.outputs.installer-path }}` and
`~/.local/share/whitaker`. The `cache-provider` input defaults to `github`;
`external` skips the built-in cache when the caller mounts both states through
another provider.

Its key is the following expression:

```text
whitaker-${{ runner.os }}-${{ runner.arch }}-${{
  steps.validate-inputs.outputs.installer-version }}-${{
  hashFiles('dylint.toml') }}-${{
  steps.validate-inputs.outputs.cargo-home }}
```

The `cargo-home` input defaults to `~/.cargo` and controls the cached installer
location. The step expands a leading `~` against `HOME`, validates the path,
adds the Windows executable suffix when required, and records the installer
path for the cache and later execution. The `installer-version` input defaults
to `0.2.7`.

On a miss, a `Resolve Whitaker release` step selects the runner's supported
release target and resolves the expected digest, then dedicated
`Download Whitaker release`, `Verify Whitaker release`,
`Extract Whitaker installer`, and `Install Whitaker installer` steps each
perform one part of the lifecycle. Unsupported platforms, missing assets, and
checksum failures stop the action. There is deliberately no Cargo or
source-build fallback. The contract is covered by the test suite in
`.github/actions/install-whitaker/tests/`, including cache ownership, official
release selection, cache reuse, digest precedence, and failure boundaries.

An external volume must mount the suite's parent (`~/.local/share`) rather than
the terminal `~/.local/share/whitaker` path. Whitaker treats an absent child as
a fresh install and an existing child as a Git checkout; a cache volume makes
its mount point exist even when empty, so mounting the child causes the
installer to attempt `git pull` in a non-repository.

### Digest manifest and trust anchor

The archive's SHA-256 digest is pinned in
`.github/actions/install-whitaker/installer-digests.sha256`, a plain
`sha256sum` manifest that sits beside `action.yml` and is reviewed with it.
Each line pairs a digest with an asset filename, for example:

```text
78959394c6bbf77eb80ce7f6818d1dedabea68224a3603b3481ee927f8be9fa0  whitaker-installer-aarch64-apple-darwin-v0.2.7.tgz
```

This pinned manifest is the trust anchor, not the release's own `.sha256`
sidecar. The `Verify Whitaker release` step still downloads the sidecar and
compares it with the archive digest it just verified, but only as a consistency
check: a compromised release could publish a matching sidecar for a tampered
archive, whereas it cannot change a digest already committed to this repository.

To extend the manifest, compute each new digest locally from an independently
downloaded archive with `sha256sum`, then cross-check it against the release's
own `.sha256` sidecar for that asset. A disagreement between the two blocks the
change. Never copy a digest from the sidecar alone; the sidecar is a check on
the locally computed digest, not a source for it.

The optional `installer-sha256` input supplies a digest for an asset the
manifest does not pin. The `Resolve Whitaker release` step applies a
manifest-first precedence rule: when the manifest pins the resolved asset, that
pinned digest is the anchor (`whitaker-installer.trust-anchor=pinned`), and a
supplied `installer-sha256` that disagrees with it is rejected before any
download (`whitaker-installer.digest=conflict`). Only when the manifest does
not pin the asset does a supplied `installer-sha256` become the anchor
(`whitaker-installer.trust-anchor=input`). An asset with neither anchor fails
before any download (`whitaker-installer.digest=unpinned`).

### Runner requirements

The action targets the runner operating-system and architecture pairs the
`Resolve Whitaker release` step's case statement maps to a release target:
Linux X64, Linux ARM64, macOS X64, macOS ARM64, and Windows X64. Every step
declares `shell: bash`, so the runner must provide Bash.

Beyond Bash, the runner must provide:

- `curl`, for downloading the release archive and its `.sha256` sidecar.
- A SHA-256 utility: the `Verify Whitaker release` step uses `sha256sum` when
  it is present and falls back to `shasum -a 256` otherwise, as it does on
  macOS runners.
- `tar`, which extracts both archive formats. bsdtar, the bundled `tar` on
  Windows and macOS runner images, reads zip archives as well as gzip ones, and
  GNU tar detects gzip without an explicit flag, so the same
  `tar -xf ... --strip-components=1` invocation works across every supported
  pair. `unzip` is deliberately not required, since it is absent from some
  Windows runner images.
- `RUNNER_TEMP` set to a writable directory. The `validate-inputs` step
  rejects an unset `RUNNER_TEMP` alongside the action's other input
  preconditions, so the action fails before any download, because the download
  is staged beneath it.

### Resolution and publication split

`Resolve Whitaker release` and `Publish Whitaker resolution` divide the
release-resolution lifecycle into a pure query and its one externally visible
consumer:

- `Resolve Whitaker release` is a thin adapter. It runs
  `scripts/resolve-release.sh`, captures everything the script prints on
  stdout, and writes that captured record to the step's `resolution` output. It
  has no other effect, beyond an `ERR` trap that reports a genuine internal
  failure (the script being unreadable, or a shell builtin such as `awk` dying)
  rather than an expected resolution outcome.
- `Publish Whitaker resolution` is the only step that turns the record into
  anything a caller or reviewer can observe: it writes the step outputs later
  steps consume (`needs-install`, `asset`, `extension`, `installer-name`,
  `expected-sha`, `trust-anchor`, `staging-dir`), emits job-summary metrics,
  prints `::notice` and `::error` annotations, and decides whether the job
  fails.

Keeping resolution pure and separate from publication lets the action's test
suite exercise `resolve-release.sh` directly, without stubbing GitHub Actions
outputs, annotations, or the job summary.

### The `resolve-release.sh` contract

`scripts/resolve-release.sh` reads named environment variables set by
`Resolve Whitaker release` (`RUNNER_OPERATING_SYSTEM`, `RUNNER_ARCHITECTURE`,
`WHITAKER_DIGEST_MANIFEST`, `WHITAKER_INSTALLER_PATH`,
`WHITAKER_INSTALLER_SHA256`, `WHITAKER_INSTALLER_VERSION`,
`WHITAKER_INSTALLER_VERSION_PATH`, and `WHITAKER_STAGING_DIR`) and prints a
`key=value` record on stdout, one field per line. It writes no file, emits no
job-summary metric, and prints no workflow annotation; every externally visible
effect belongs to the publication step. An expected resolution failure — an
unsupported runner, or a digest that cannot be resolved — is reported as an
`error` record on stdout, not as a non-zero exit. A non-zero exit is reserved
for a genuine internal failure, which is why the script omits `errexit` and
lets its caller own the `ERR` trap.

The record's `status` field takes one of three values:

- `cached` — an executable installer is already present at
  `WHITAKER_INSTALLER_PATH` and its version marker names the requested version.
  No other field is printed.
- `install` — the release must be downloaded. The record also carries
  `asset`, `extension`, `installer-name`, `expected-sha`, `trust-anchor`, and
  `staging-dir`. When a cached installer exists but names a different version,
  the record also carries `stale-version`.
- `error` — resolution could not proceed. The record also carries
  `error-kind` and `error-message`.

The `error-kind` field takes one of five values, each produced by a different
check in the script:

- `unsupported-runner` — the runner operating-system and architecture pair
  has no mapped release target. This is checked before any cache reuse, so a
  cached installer cannot mask an unsupported runner.
- `digest-conflict` — the manifest pins a digest for the resolved asset and
  the supplied `installer-sha256` disagrees with it.
- `unpinned-digest` — the asset has neither a pinned digest nor a supplied
  `installer-sha256`.
- `manifest-unreadable` — the digest manifest exists but could not be read.
- `version-marker-unreadable` — the installed-version marker exists but could
  not be read.

The two `unreadable` kinds matter more than they look. An absent manifest or
marker is a result, meaning nothing is pinned or nothing is cached, but one
that exists and cannot be read is a failure. Degrading it to the absent case
would silently fall back to the caller's digest, report a pinned asset as
unpinned, or reuse a cached installer of unknown version. The lookup helpers
therefore return a distinct non-zero status for a read failure, and every
caller propagates it.

`Publish Whitaker resolution` maps `digest-conflict`, `unpinned-digest`,
`manifest-unreadable`, and `version-marker-unreadable` to the
`whitaker-installer.digest=conflict`, `whitaker-installer.digest=unpinned`,
`whitaker-installer.digest=unreadable`, and
`whitaker-installer.cache-entry=unreadable` metrics respectively;
`unsupported-runner` has no dedicated metric and falls through to the generic
`whitaker-installer.failure=install` metric.

### Version marker and cache reuse

Alongside the installer binary, the action writes a version marker file
(`.whitaker-installer-version`, recorded in
`steps.validate-inputs.outputs.installer-version-path`) and caches it beside
the installer. `resolve-release.sh` reuses a cached installer only when this
marker names the exact version requested by `installer-version`; any other
content, including no file at all, is treated as a cache miss for reuse
purposes.

This check matters most for `cache-provider: external`. With the built-in
`github` cache, a `installer-version` bump changes the cache key, so a stale
installer is never restored in the first place. With an external, caller-
mounted Cargo home, nothing rotates the mount when `installer-version` changes:
without the marker check, a persistent Cargo home would keep serving an
installer built for an older version indefinitely, regardless of what the
caller now requests. The marker check makes version correctness independent of
how the cache is provisioned.

A stale or absent marker is reported as `whitaker-installer.cache-entry=stale`
in the job summary, and the action falls through to a freshly verified download
of the requested version.

### Transfer and job-summary telemetry

`Download Whitaker release` fetches the release archive and its `.sha256`
sidecar in two separate `curl` invocations, and reports each transfer with both
a `::notice title=Whitaker installer transfer::` annotation and a
`whitaker-installer.transfer.<part>=...` job-summary metric, where `<part>` is
`archive` or `sha256`. Each report names the outcome (`ok` or `failed`), the
HTTP status code, the downloaded byte count, the elapsed time in seconds, and
the retry attempt count.

The attempt count depends on `curl`'s `num_retries` write-out variable, which
was added in curl 8.9.0. The step compares the runner's `curl --version` against
`8.9.0` before adding `%{num_retries}` to its `--write-out` format, and reports
`attempts=unknown` when the runner's curl predates that version.

The job summary carries these metric names, read from `action.yml`:

- `whitaker-installer.cache=<state>`, where `<state>` is `disabled`, `hit`, or
  `miss`.
- `whitaker-installer.cache-entry=stale`.
- `whitaker-installer.path=cache`, `whitaker-installer.path=official-release`.
- `whitaker-installer.trust-anchor=<anchor>`, where `<anchor>` is `pinned` or
  `input`.
- `whitaker-installer.digest=conflict`, `whitaker-installer.digest=unpinned`,
  `whitaker-installer.digest=mismatch`,
  `whitaker-installer.digest=sidecar-mismatch`,
  `whitaker-installer.digest=verified`.
- `whitaker-installer.transfer.archive=...`,
  `whitaker-installer.transfer.sha256=...`.
- `whitaker-installer.failure=resolve`, `whitaker-installer.failure=install`,
  `whitaker-installer.failure=execution`.
- `whitaker-installer.result=success`.

## `upload-codescene-coverage` check-mode contract

The `gate-applicability` step runs only when `inputs.mode` is `check`. It
compares the non-empty `github.base_ref` with
`github.event.repository.default_branch`. When they differ, it writes
`skip=true` to `GITHUB_OUTPUT` and emits a warning explaining that the base is
not an analysed branch. An empty base does not trigger this skip, which keeps
the applicability check usable outside a pull-request event.

The applicability output is the boundary for the rest of the action. Every
following step — coverage-path resolution, installer download, GitHub artefact
upload, cache and CLI installation, PATH setup, and the upload/check commands —
must require `steps.gate-applicability.outputs.skip != 'true'`. Do not add a
check-mode step outside that guard unless it is deliberately meant to run for
skipped pull requests.

The check command is an observable diagnostic contract. After validating the
CLI, coverage file, and LCOV suffix, run
`cs-coverage check --verbose --coverage-files "$file"` directly so its native
standard-output and standard-error streams remain intact. Put the invocation in
an `if` condition; in the failure branch, capture `$?` as the first command,
add the uploaded-base explanation when the status is `2`, then
`exit "$status"`. This preserves every CLI failure status rather than masking
it with diagnostic handling. The behavioural contract is covered by the
[check-mode tests](../.github/actions/upload-codescene-coverage/tests/test_check_mode.py).

## `setup-rust` cargo-binstall Pinning

The `setup-rust` action pins `cargo-binstall` by downloading
`install-from-binstall-release.sh` from a tagged cargo-binstall release and
checking the installer script against a fixed SHA-256 digest. Treat the version
tag and checksum as a pair: update both in the same change and keep the
checksum tied to the installer script at that tag.

`BINSTALL_VERSION` must be exported in the install step. The installer script
is executed by a child `bash` process and reads `BINSTALL_VERSION` from its
environment to decide whether to download from `releases/download/<version>/`
or from `releases/latest/download/`. If the value is only a shell variable, the
child process sees it as empty and silently uses `latest`, defeating the pin.

After the installer runs, the action verifies the installed binary with
`cargo-binstall -V` and fails with the actual version in the log if it does not
match the pinned release. Keep that runtime check in sync with
`BINSTALL_VERSION` whenever the pin changes.

## `generate-coverage` whole-workspace test selection

`all-features`, `all-targets`, and `doctests` exist so a repository can make
the coverage job its only test execution instead of running the suite twice.
All three default to off, so a caller that does not set them sees the previous
behaviour exactly.

`feature_selection_args` in
[`run_rust.py`](../.github/actions/generate-coverage/scripts/run_rust.py) is the
single place feature flags are decided, and both the coverage command and the
doc-test command call it. Keep it that way: the precedence rule only holds if
one function owns it. That rule is that `all_features` wins outright. It
supersedes `with_default`, so `--all-features --no-default-features` can never
be emitted, and it is rejected outright alongside a non-empty feature list,
because silently widening a caller's named selection would misreport what ran.

Doc tests are a separate Cargo target kind, so `--all-targets` does not reach
them and `--doc` cannot be combined with it. `run_doctests` therefore issues a
plain `cargo test --doc --workspace` after the instrumented run, forwarding the
feature selection but not `--all-targets`. That run is uninstrumented: it
contributes no coverage and exists so a broken doc test fails the job.

A caller's `RUSTFLAGS` survives into every Cargo invocation because
`_build_cargo_env` starts from a copy of `os.environ` and neither the coverage
overrides nor `_CARGO_COVERAGE_ENV_UNSETS` names that variable. Repositories
running warnings-denied coverage depend on this; the guarantee is asserted in
[`test_generate_coverage_feature_selection.py`](../.github/actions/generate-coverage/tests/test_generate_coverage_feature_selection.py),
which also holds the manifest contract and the rendered cargo commands.

### `run_rust.py` boundaries

The script is arranged so that ambient state is read once and every other
function is a function of its arguments.

<!-- markdownlint-disable MD013 -->
| Symbol | Role |
| --- | --- |
| `feature_selection_args` | Pure builder. Returns the Cargo feature flags and emits nothing. |
| `feature_selection_diagnostics` | Pure query. Returns the `(error, warning)` a selection deserves. |
| `check_feature_selection` | The only function that reports a selection or raises `typer.Exit`. |
| `_resolve_targets`, `_resolve_features`, `_resolve_cucumber` | Take the raw inputs and an explicit environment mapping; return a frozen record each. |
| `_run_coverage` | Runs the instrumented build, then any cucumber and doc-test runs. |
| `run_doctests` | Uninstrumented `cargo test --doc --workspace` with the same feature selection. |
| `main` | Reads `os.environ` once, assembles the records, checks the selection, and reports. |
<!-- markdownlint-enable MD013 -->

Keep the split. The precedence rule holds only because one builder owns it and
one boundary reports it, and the resolvers stay testable only while the
environment arrives as an argument rather than through `os.environ`.
`_required_env` and `_env_bool` in `common.py` accept the same optional mapping
for that reason.

## `generate-coverage` cargo-binstall Pinning

`generate-coverage` provisions its own `cargo-binstall` in the "Ensure
cargo-binstall" step before installing `cargo-llvm-cov`. It follows the same
pinning discipline as `setup-rust`: `BINSTALL_VERSION` and the installer-script
`BINSTALL_SHA256` are a pair and must be updated together.

The step is idempotent and verifies the version on both paths:

- **Fast path** — if `cargo-binstall` is already on `PATH`, its `-V` output is
  matched against the pinned version. On a match the step reuses the binary and
  exits without any network access; on a mismatch it logs the discrepancy and
  falls through to a pinned reinstall.
- **Install path** — the checksum-pinned installer script is downloaded and its
  SHA-256 verified before execution, and the freshly installed binary's version
  is re-checked so a wrong installed version fails the step.

Both paths are exercised by behavioural tests in
`.github/actions/generate-coverage/tests/test_scripts.py`, which execute the
extracted step body against fake `cargo-binstall` binaries and installers
rather than asserting on the step's source text.

### CARGO_HOME resolution and PATH handling

The "Ensure cargo-binstall" step derives the active Cargo bin directory at
runtime:

```bash
cargo_home_bin="${CARGO_HOME:-$HOME/.cargo}/bin"
```

This respects any custom `CARGO_HOME` set by the caller. The resolved path is
used for three purposes:

1. **GITHUB_PATH** – when `GITHUB_PATH` is set, the resolved bin directory is
   appended so that subsequent workflow steps see the binary on their `PATH`.
2. **Current-step PATH** – the bin directory is prepended to the *current*
   shell's `PATH` (guarded by a `case ":$PATH:"` check to avoid duplication) so
   that in-step commands can also find the binary.
3. **Absolute-path verification** – `cargo-binstall` is invoked via its
   resolved absolute path (`"$cargo_binstall"`) rather than as an unqualified
   command, ensuring that verification succeeds even when the bin directory has
   not yet been propagated to the shell's `PATH` by other means.

Keep `cargo_home_bin` resolution and the `BINSTALL_VERSION` pin in sync: both
must reflect the same intended installation location and version whenever the
pin is updated.

## `generate-coverage` cargo-nextest installation

`generate-coverage` installs `cargo-nextest` through
`.github/actions/generate-coverage/scripts/install_cargo_nextest.py`, invoked
by the "Install cargo-nextest" step for Rust or mixed-language runs when
`use-cargo-nextest` is `true`. The script never invokes Cargo or
`cargo-binstall`; a missing or unverifiable official archive is a hard failure
with no fallback.

The script pins the `cargo-nextest` release version in `CARGO_NEXTEST_VERSION`
and resolves the official release target and expected archive and binary
checksums using `_platform_key()`. On Linux, `_platform_key()` calls
`_is_musl()` to choose between `linux-<arch>-gnu` and `linux-<arch>-musl` keys.
Linux x86_64 is split into two keys for this reason:

- `linux-x86_64-gnu` for the `-x86_64-unknown-linux-gnu` archive.
- `linux-x86_64-musl` for the `-x86_64-unknown-linux-musl` archive.

This distinction is intentional because the upstream artefacts are built
against different libc ABIs, and validating against the wrong digest can block
installs even when the same version number is used.

`_is_musl()` wraps libc probing in one place via injectable `ctypes.CDLL`
/symbol lookup and surfaces probe failures through the normal error path, so
orchestrating code consumes a concrete `typer.Exit` from
`_release_for_platform()` and keeps loader details local to the installer.

If an already-resolvable `cargo-nextest` binary (found on `PATH` or in the
Cargo bin directory) already matches the pinned executable digest in
`CARGO_NEXTEST_SHA256`, the script reuses it without downloading anything.
Otherwise it downloads the selected archive directly from the pinned
`nextest-rs/nextest` GitHub release, verifies the archive's SHA-256 against the
pinned digest in `CARGO_NEXTEST_RELEASE_ASSETS`, extracts only the expected
executable into a temporary file, verifies that executable against the pinned
digest in `CARGO_NEXTEST_SHA256`, then replaces the destination atomically.

Keep `CARGO_NEXTEST_VERSION` and both checksum tables
(`CARGO_NEXTEST_RELEASE_ASSETS` and `CARGO_NEXTEST_SHA256`) in sync: update the
version and every pinned archive and binary digest together.

### `ReleaseAsset` and `emit_metric` boundaries

Two small constructs in `install_cargo_nextest.py` are worth understanding
before changing it:

- `ReleaseAsset` is a `typing.NamedTuple` that pins one release archive's
  `target`, `extension`, and `sha256` digest. Its `filename` property derives
  the official archive filename
  (`cargo-nextest-{CARGO_NEXTEST_VERSION}-{target}.{extension}`) from those
  fields and the module-level `CARGO_NEXTEST_VERSION`, so the filename can
  never drift from the pinned target and extension it was built from.
- `emit_metric()` is the single place that appends a bounded line to
  `$GITHUB_STEP_SUMMARY`. Every metric in the script goes through this one
  function, and it does nothing when `GITHUB_STEP_SUMMARY` is unset, which is
  the case when running the script or its tests outside a GitHub Actions job.

The script emits these `cargo-nextest.` metric names, read from
`install_cargo_nextest.py`:

- `cargo-nextest.download=ok`, `cargo-nextest.download=failed`.
- `cargo-nextest.archive-digest=ok`, `cargo-nextest.archive-digest=mismatch`.
- `cargo-nextest.binary-digest=ok`, `cargo-nextest.binary-digest=mismatch`.
- `cargo-nextest.install=ok`, `cargo-nextest.install=failed`,
  `cargo-nextest.install=reused`.

## `stage-release-artefacts` Action Architecture

### Staging Pipeline

The `stage-release-artefacts` action is implemented by
`.github/actions/stage-release-artefacts/scripts/stage.py`, which loads a TOML
configuration and delegates staging to `stage_common.pipeline.stage_artefacts`.
The pipeline renders configured source and destination templates, copies each
matched artefact into the staging directory, writes checksum sidecar files, and
returns a `StageResult`. The CLI owns infrastructure concerns: it reads
`GITHUB_WORKSPACE` and `GITHUB_OUTPUT`, emits GitHub Actions warning
annotations for skipped optional artefacts, and writes workflow outputs.

`stage_common.config.load_config` requires callers to pass the workspace
explicitly via `workspace=...`; it no longer reads `GITHUB_WORKSPACE` itself.
This keeps configuration loading independent from the process environment. The
CLI remains responsible for resolving `GITHUB_WORKSPACE` with
`require_env_path` and injecting that path into `load_config` before staging
(issue `#266`).

`_collect_artefacts` owns the collection phase. It iterates over the configured
artefacts, records the staged paths, builds the map of named outputs, and
collects checksums keyed by staged relative path. `stage_artefacts` validates
reserved output names, resolves optional PowerShell sidecar metadata, logs
start and completion records with counts and elapsed time, and returns a
`StageResult` without writing `GITHUB_OUTPUT`.

### Output Data

`stage_common.output.StagingOutputData` is the parameter object passed to
`prepare_output_data`. It keeps the output formatter explicit without growing
the function argument list. The object contains the staging directory, staged
paths, named output paths, checksum map, and the optional `powershell_help_dir`.
`prepare_output_data` serializes path values with `Path.as_posix()` so
workflow outputs use forward slashes consistently across platforms.

The CLI converts `StageResult` into `StagingOutputData` immediately before
calling `write_github_output`. Tests that assert output-file structure redact
absolute staging paths before snapshot comparison so path-sensitive output
remains deterministic across runners.

### PowerShell Help Directory

`_resolve_powershell_help_dir` only exports a PowerShell module directory when
`ps-module-name` names a single direct child of the staging directory and at
least one staged file exists below that directory. Empty names, `"."`, `".."`
and names containing path separators return `None`. The resolved module
directory must also have `staging_dir.resolve()` as its parent, which prevents
parent-directory traversal and nested module paths from being exported. The
pipeline logs the reason a PowerShell directory was not exported, including
empty input, invalid module names, and missing staged files below the module
directory.

The action metadata and README document the public `ps-module-name` input and
`powershell_help_dir` output. Keep that public contract in sync with the
internal rules above whenever changing PowerShell sidecar staging.

### Observability

Each staging run has a correlation ID (`corr_id`) generated by `stage.py` for
CLI execution or by `stage_common.pipeline.stage_artefacts` for direct callers.
Pipeline INFO, WARNING, and DEBUG records include that `corr_id` so start,
per-artefact, PowerShell-resolution and failure records can be grouped in CI
logs.

The staging pipeline emits INFO records when staging starts and completes. The
start record includes the target, artefact count, staging directory and
`ps_module_name`; the completion record includes staged, skipped, checksum and
output counts, elapsed time in `elapsed_ms`, and the resolved
`powershell_help_dir` value. PowerShell resolution emits INFO records for empty
names, rejected module names, missing files below the requested module
directory, and successful resolution.

Skipped optional artefacts are surfaced as WARNING-level GitHub annotations by
`stage.py`, keeping optional sidecar misses visible in workflow logs without
turning them into failures.

DEBUG records cover each resolved staged artefact, including source path,
destination path and checksum digest, plus the PowerShell module-directory
existence checks. `stage.py` logs exceptions with the same `corr_id` before
emitting the GitHub Actions `::error` annotation. Use pytest's log options to
enable DEBUG output during local investigation or CI repro jobs:

```bash
pytest -o log_cli=true --log-level=DEBUG
```

## Workflow Test Harness (`tests/workflows/conftest.py`)

### Runtime Probing

The workflow test harness determines whether `act` and a compatible container
runtime are available before running tests. The probe result is represented by:

<!-- markdownlint-disable MD013 -->
| Symbol                    | Type                             | Role                                                                                       |
| ------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------ |
| `ActRuntimeStatus`        | frozen dataclass                 | Holds `available: bool`, `reason: str`, `env: dict[str, str]`.                             |
| `_probe_act_runtime`      | `(environ?) -> ActRuntimeStatus` | Execute a fresh probe against the given environment mapping (defaults to `os.environ`).    |
| `_get_act_runtime_status` | `() -> ActRuntimeStatus`         | Return the cached probe result, performing the probe on first call.                        |
| `_act_command`            | `(environ?) -> str`              | Return the act executable path from the `ACT` environment variable, defaulting to `"act"`. |
<!-- markdownlint-enable MD013 -->

`_get_act_runtime_status` is decorated with `@functools.cache` so the probe
runs at most once per process. Tests that need to observe a different runtime
environment must call `_probe_act_runtime(environ)` directly with an explicit
mapping, bypassing the cache.

`ActRuntimeStatus.env` carries any additional environment variables that must
be injected into the `act` subprocess - currently used to forward `DOCKER_HOST`
when a healthy Podman socket is discovered automatically.

### Skip Markers

<!-- markdownlint-disable MD013 -->
| Marker                       | Condition                                                    |
| ---------------------------- | ------------------------------------------------------------ |
| `skip_unless_act`            | Skip when `_get_act_runtime_status().available` is `False`.  |
| `skip_unless_workflow_tests` | Skip when `ACT_WORKFLOW_TESTS` is not set to a truthy value. |
<!-- markdownlint-enable MD013 -->

## Mutation-Testing Reusable Workflows

Two reusable workflows provide scheduled, informational mutation testing for
callers: `.github/workflows/mutation-cargo.yml` (Rust,
[cargo-mutants](https://mutants.rs/)) and
`.github/workflows/mutation-mutmut.yml` (Python,
[mutmut](https://mutmut.readthedocs.io/)). Caller-facing usage lives in
[mutation-cargo-workflow.md](mutation-cargo-workflow.md) and
[mutation-mutmut-workflow.md](mutation-mutmut-workflow.md); design history,
empirical findings, and decisions live in the
[execplan](execplans/add-mutation-testing-workflows.md).

Internals for maintainers:

- All non-trivial logic lives in `workflow_scripts/` helper scripts
  (Cyclopts, `INPUT_*` environment configuration, plumbum):
  `mutation_detect_changes.py` (change-detection guard and shard-matrix
  construction, shared by both workflows), `mutation_run_cargo.py`
  (cargo-mutants invocation and the informative exit-code contract: 0/2/3
  succeed, everything else fails with the tool's code),
  `mutation_summarize_cargo.py` (merges shard `outcomes.json` artefacts and
  renders the job summary), and `mutation_run_mutmut.py` (module-glob scoping,
  results parsing, and summary in one pass — mutmut has no shard support).
- Tool version pins default in the workflows because both report
  formats are unstable; a version bump must be paired with a parser check
  (`outcomes.json` fields for cargo-mutants; the `mutmut results --all true`
  line format for mutmut).
- Unit tests fake the `cargo`/`uv` boundary with POSIX shell shims on
  `PATH`; those tests are skipped on Windows (the workflows only run on
  `ubuntu-latest`). Property-based tests in
  `workflow_scripts/tests/test_mutation_properties.py` cover the bucketing,
  translation, and parsing invariants.
- The act integration tests
  (`tests/workflows/test_mutation_workflows.py`) exercise the change-detection
  skip path end-to-end; the mutation-run path cannot be act-tested because stub
  binaries cannot be injected onto a `workflow_call` job's `PATH`.
- Workflow-source resolution lives in the
  `.github/actions/resolve-workflow-source` composite action (see its README).
  Because that action performs the SHA resolution itself, the reusable
  workflows reference it by a hardcoded full-commit pin that must be bumped
  manually whenever the action changes — it is the one reference that cannot
  participate in the caller's version lockstep. Its act short-circuit and OIDC
  fail-fast branches are exercised by
  `tests/workflows/test_resolve_workflow_source.py`; the OIDC happy path is
  validated by every real run of the consuming workflows.

## Running the Test Suite

```bash
make test          # full suite
make check-fmt     # Ruff formatting check
make typecheck     # Ty, resolving imports through .venv
make lint          # Ruff lint + action-validator + markdownlint
```

## `install-nixie` Action Maintenance

The composite action boundary is `.github/actions/install-nixie/action.yml`. It
requires `uv`, `curl`, and runner-provided archive and checksum tools, and
exposes three pins: `nixie-version` defaults to `1.1.0`, `merman-version`
defaults to `0.7.0`, and `python-version` defaults to `3.14`. Keep those public
inputs and their defaults synchronized with the action README and users' guide.

Merman is a release-asset policy, not a package-manager policy. The action
supports only Merman 0.7.0 and maps `Linux/X64`, `macOS/X64`, `macOS/ARM64`, and
`Windows/X64` to an official `Latias94/merman` archive and literal Secure Hash
Algorithm 256-bit (SHA-256) archive and executable digests. The Windows archive
is verified with PowerShell's `Get-FileHash` and extracted by `Expand-Archive`
through Git Bash's path conversion. The action stores Merman at
`${XDG_CACHE_HOME:-${HOME}/.cache}/merman/<version>/bin` (`.exe` on Windows)
and revalidates its pinned executable digest before every cache reuse. Callers
that persist the action's cache must include `~/.cache/merman`. On a cache miss
it downloads, checksums, extracts, and installs the release asset. Do not add
Cargo, `cargo binstall`, or a source-build fallback. Adding a new Merman
release or runner pair requires an official release asset, an independently
reviewed digest, focused tests, and synchronized user-facing documentation;
fail closed until all four exist.

Nixie reconciles its exact package version with ordinary `uv tool install`.
After obtaining the `uv tool dir --bin` directory, the action checks for the
`nixie` executable shim and repeats the installation with `--force` only when
that shim is absent. Do not add a `nixie --version` probe. Both Merman and
Nixie executable checks must succeed before their directories are written to
`GITHUB_PATH`. On Windows, convert the native `uv` directory to a Git Bash path
for executable checks, while retaining the native directory for `GITHUB_PATH`.

`.github/actions/install-nixie/tests/test_action.py` is the behavioural test
boundary. Changes must retain coverage for a warm Merman cache, the official
download/checksum path, checksum and unsupported-version failures, normal and
forced Nixie reconciliation, Nixie installer failure, and the no-PATH-export
failure boundary.

## `rust-build-release` Action Architecture

### Man-Page Path Strategy

The `rust-build-release` composite action stages compiled Rust man pages for
release packaging. Man pages are expected at a deterministic location:

```text
target/generated-man/<TARGET>/<PROFILE>/<bin>.1
```

This path is written by the consuming project's `build.rs` script. The build
script derives `target/` from `CARGO_TARGET_DIR` when that variable is set (as
it is when `cross` mounts the workspace inside a Docker container), and
otherwise walks five ancestor levels of Cargo's hash-dependent `OUT_DIR`.

If the stable path is absent the staging step falls back to scanning
`target/<triple>/release/build/*/out/` - the legacy Cargo build-output location

- and emits a `::warning::` annotation. Zero or multiple matches in the legacy
location are fatal errors.

When `skip-man-page-discovery` is set to `'true'`, the staging step bypasses
all man-page discovery and installation; no `man-path` output is written to
`GITHUB_OUTPUT`. When it is `'false'` (the default), the existing stable-path
and legacy fallback behaviour applies. Callers that generate man pages in a
post-`cross build` step, for example via `cargo-orthohelp`, must set this input
to `'true'` and handle staging themselves.

### Fingerprint Invalidation

`build.rs` emits `cargo:rerun-if-env-changed=CARGO_TARGET_DIR` so that a change
in the Docker bind-mount target directory (e.g. between cached and uncached CI
runs) forces the build script to rerun and regenerate the man page at the
correct stable path.

### Build Observability

`build.rs` emits a `cargo:warning=writing man page to ...` diagnostic so that
the chosen stable path is visible in the `cargo build` log. The staging step
emits
`::warning::stable man-page path ... was absent; using legacy fallback ...`
when the fallback activates.

### Testing

Shell-script behaviour of the staging step is exercised by
`.github/actions/rust-build-release/tests/test_stage_script_behaviour.py`,
which extracts the `stage-artefacts` run block from `action.yml`, parametrizes
it, and runs it under bash. Five scenarios are covered: stable path present,
legacy fallback, missing man page (error), multiple legacy matches (error), and
skip mode (no man-page staging, binary only). Tests are automatically skipped
on Windows.

### Nested `setup-rust` Cache Passthrough

The `cache-provider` and `use-sccache` inputs exist only to reach the nested
`setup-rust` step; this action neither restores nor saves a cache. Keep the
forwarding verbatim (`${{ inputs.cache-provider }}` and
`${{ inputs.use-sccache }}`) rather than deriving a value, so the nested action
remains the single place that decides what each mode disables. The "Validate
cache provider" step duplicates the nested guard on purpose: it runs before
toolchain setup, so a typo fails in seconds instead of after a toolchain
install. When the accepted provider names change, update both manifests and
their guards together.

The nested pin is a `main` revision rather than the `setup-rust-v1` tag,
because that tag predates the two inputs. When bumping it, confirm the target
revision still declares them, update `EXPECTED_SETUP_RUST_SHA` in
[`test_setup_rust_reference.py`](../.github/actions/rust-build-release/tests/test_setup_rust_reference.py)
in the same change, and prefer the tagged commit once `setup-rust-v1` moves
past the current pin.

### RUSTFLAGS Export

Both `setup-rust` and `rust-build-release` expose a `rustflags` input, but they
wire it differently. `setup-rust` forwards the input straight through to each
of its three `actions-rust-lang/setup-rust-toolchain` invocations, so what
happens to an inherited `RUSTFLAGS` is that nested action's decision.
`rust-build-release` instead exports the value itself, in an "Export caller
RUSTFLAGS" step that runs *before* its own pinned nested `setup-rust` step (see
`.github/actions/rust-build-release/action.yml`), so that step's
`setup-rust-toolchain` — which only applies its `-D warnings` default when
`RUSTFLAGS` is unset — defers to the caller's value. The design rationale for
this split lives in section 3.1.3, "Caller-Controlled `RUSTFLAGS`", of the
[Rust Build and Release Pipeline design](rust-build-release-pipeline.md); the
caller-facing usage is in the [users' guide](users-guide.md). This section
covers the implementation detail a maintainer needs to change the export step
safely.

#### Precedence guard

The export step is skipped entirely by `if: inputs.rustflags != ''`, but even
when it runs it must not clobber a `RUSTFLAGS` the caller already exported. It
guards with `[[ ${RUSTFLAGS+x} ]]`, which is true whenever `RUSTFLAGS` is set,
including to the empty string, so an inherited value — empty or not — always
wins over the input. `setup-rust` has no equivalent guard; forwarding the empty
string to it leaves `RUSTFLAGS` alone only because `setup-rust-toolchain`
treats an empty forwarded value as "unset".

#### Bash 3.2 compatibility

`[[ ${RUSTFLAGS+x} ]]` is used rather than the more idiomatic
`[[ -v RUSTFLAGS ]]` because `-v` needs Bash 4.2 and macOS runners ship Bash
3.2, which cannot parse that conditional primary. Both forms treat an inherited
empty value as set. Keep this constraint in mind for any future edit to this or
similar shell fragments in the two actions: parameter expansion of the
`${NAME+x}` form, not `-v`, is the portable way to test "is this variable set".

#### `GITHUB_ENV` heredoc safety

The step writes `RUSTFLAGS` to `GITHUB_ENV` as a heredoc rather than a plain
assignment because the value may contain newlines. The delimiter is derived
from 16 random bytes (`od -An -N16 -tx1 /dev/urandom`) and checked against the
value with `grep -qxF` before use. If a value contained the delimiter on a line
of its own, that line would close the heredoc block early, and whatever
followed would be read back by the runner as further environment-file commands
— an injection route, not just a formatting bug. The step retries with a fresh
candidate up to three times and fails the step, rather than writing an unsafe
delimiter, if all three collide.

#### RUSTFLAGS export observability

The step logs three kinds of event, all to `stderr`:

- deferral to an inherited value ("RUSTFLAGS already set; leaving the
  inherited value in place");
- each delimiter-collision attempt, numbered out of the fixed retry budget
  ("RUSTFLAGS delimiter attempt `N` of 3 collided with the value; retrying");
- the successful export, also numbered ("RUSTFLAGS exported from the
  rustflags input on attempt `N` of 3").

It deliberately never logs the `RUSTFLAGS` value itself or a colliding
delimiter candidate: a candidate only collides because the value contains it as
a substring, so echoing the candidate would leak a line of the caller's
`RUSTFLAGS` into the CI log.

#### RUSTFLAGS export testing

`.github/actions/rust-build-release/tests/test_rustflags_export.py` extracts
and runs the export step's shell fragment under bash. It covers the precedence
guard (including an inherited empty value), the heredoc round-trip for
adversarial payloads via Hypothesis properties, the delimiter-collision retry
and give-up paths (using a stubbed `od` to make a collision reachable), and
that neither the value nor a colliding candidate reaches the log.
`.github/actions/rust-build-release/tests/test_manifest_input_step.py` checks
the manifest's declared shape instead: the `rustflags` input's empty default,
the export step's `if` condition and `RBR_RUSTFLAGS` wiring, the
`${RUSTFLAGS+x}` guard's presence in the run script, and that the export step
precedes toolchain setup.
