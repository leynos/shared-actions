# Users' Guide: Rust Caching, Rust Flags, CodeScene Coverage, and Install Nixie

This guide explains cache ownership and the `rustflags` inputs exposed by the
`setup-rust` and `rust-build-release` composite actions, and the CodeScene
coverage modes provided by `upload-codescene-coverage`. It covers why these
inputs and modes exist and how to configure them for common scenarios. It also
documents how to use the `install-nixie` action.

## Related documents

- [Rust Build and Release Pipeline design](./rust-build-release-pipeline.md)
  – see "3.1.3 Caller-Controlled `RUSTFLAGS`" for the underlying design
  rationale and implementation notes.
- [`setup-rust` README](../.github/actions/setup-rust/README.md) – full input
  and output tables.
- [`rust-build-release` README](../.github/actions/rust-build-release/README.md)
  – full input and output tables.
- [`install-whitaker` README](../.github/actions/install-whitaker/README.md) –
  installer input and cache details.
- [`install-mdtablefix` README](../.github/actions/install-mdtablefix/README.md)
  – platform support, cache ownership, and the pinned `cargo-binstall`.
- [Migrating to verified prebuilt CI tools](./migrating-to-verified-prebuilt-tools.md)
  – upgrade guidance for the `install-whitaker` and `generate-coverage`
  verified prebuilt tool installation.

## Node.js 24 action dependencies

`setup-rust` pins its Node.js-backed cache, sccache and MSYS2 dependencies to
revisions that support the GitHub Actions Node.js 24 runtime. This removes the
Node.js 20 deprecation warnings without changing the action's inputs or cache
configuration. See the
[`setup-rust` README](../.github/actions/setup-rust/README.md) for the pinned
revisions and current cache behaviour.

## `setup-rust` and sccache

On a non-release event with `use-sccache` enabled, `setup-rust` installs
sccache and exports `RUSTC_WRAPPER` naming it. That export matters: the sccache
action itself exports only `SCCACHE_PATH`, and Cargo routes compilation through
sccache only when `RUSTC_WRAPPER` is set, so before this a caller who did not
set the wrapper had sccache installed and never used.

A `RUSTC_WRAPPER` the caller has already set is left alone, including a
deliberately empty value, and the action says so in a notice. If `SCCACHE_PATH`
is absent the step fails rather than continuing silently, because continuing
would restore exactly the uncached build this exists to prevent. sccache
statistics are zeroed after the export, so a later `sccache --show-stats`
measures the caller's own build; if zeroing fails the wrapper still stands and
the action warns.

Each run reports one bounded `metric setup-rust.sccache.wrapper=<state>` line,
over `exported`, `exported-stats-not-zeroed`, `caller-set`, and
`missing-sccache-path`.

The action also selects the cache backend before sccache starts, and it selects
by runner. Without a backend sccache writes to local disk, which nothing
persists between jobs, so the wrapper would cost time and return an empty cache
on every run.

- **Ubicloud.** When the runner's cache URL names a private address, the
  action publishes the Ubicloud cache proxy's credentials itself and selects
  `ubicloud`. You no longer need `export-ubicloud-cache-credentials` before
  `setup-rust`; a job that still calls it first is harmless, and its exported
  credentials are recognized rather than exported again.
- **GitHub-hosted.** Any other runner selects `github`, sccache's own GitHub
  Actions backend.
- **Local.** Under nektos/act, or on a runner with no cache service at all, the
  action selects `local` disk rather than failing the build.

Your own choice still wins. An explicit `SCCACHE_GHA_ENABLED` (or
`SCCACHE_GHA_VERSION`) is left alone, `false` and an empty value included, so a
workflow that sets it at job level keeps its own value and needs no change.
Failing that, a caller-set `SCCACHE_DIR` leaves sccache on that directory.
`SCCACHE_DIR` therefore selects local disk only when `SCCACHE_GHA_ENABLED` is
unset. Caches do not cross between Ubicloud's proxy and GitHub's service, so a
fork's pull request that falls back to a GitHub-hosted runner starts with a
cold cache.

The selection is the `cache-backend` output (`ubicloud`, `github` or `local`,
and empty when sccache is not in use) and the line
`metric setup-rust.sccache.backend=<ubicloud|github|local>`. To confirm it took
effect, `sccache --show-stats` reports a `ghac` cache location, on either
service, rather than `Local disk`.

A job that must have a particular backend says so with `expect-cache`:

```yaml
- uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
  with:
    # This job runs only on Ubicloud and has no fork fallback, so a missing
    # proxy should fail it rather than fall back to local disk.
    expect-cache: ubicloud
```

`expect-cache` takes `ubicloud`, `github` or `any`, and defaults to `any`,
which never fails for want of a backend. Leave it at `any` on a job with a fork
fallback: the fork's run is meant to take whatever the GitHub-hosted runner
offers. The setting applies only when sccache is in use.

The action starts the sccache server too, in a `run:` step of its own that is
the last of its sccache steps. sccache reads its cache configuration once, when
the server starts, and never rebinds it, so every export that could change the
answer has to come first. Because the server is fresh its counters begin at
zero, and a later `sccache --show-stats` measures your build alone. If you set
`RUSTC_WRAPPER` yourself, or ran `setup-rust` earlier in the same job, the step
leaves your server running rather than restarting it and losing its counters.
The outcome is reported as
`metric setup-rust.sccache.server=<started|started-stats-not-zeroed|start-failed|caller-set|missing-sccache-path>`.

Some exports have to be put back rather than made, and that is the reason
`use-sccache: 'true'` used to be unusable on Ubicloud. The last thing
`mozilla-actions/sccache-action` does is write `ACTIONS_CACHE_SERVICE_V2=on` to
`GITHUB_ENV`, along with the runner's own `ACTIONS_RESULTS_URL` and
`ACTIONS_RUNTIME_TOKEN`. On a GitHub-hosted runner that is what you want. On
Ubicloud the v2 flag overrode the empty value
`export-ubicloud-cache-credentials` published, and the proxy serves v1, so
every write went to a service that was not holding the cache: Chutoro's
Ubicloud lane recorded 164 write errors out of 301 requests.

`setup-rust` now reads all three before those steps and writes back any that
changed, reporting
`metric setup-rust.sccache.cache-service=<restored|unchanged|absent>` and the
same over `results-url` and `runtime-token`. Only the flag does harm today; the
other two are tracked because they are the sccache-action's to change. **If you
never set a variable, the action's value stays**, including the `on` that suits
a GitHub-hosted runner.

So on Ubicloud, leave `use-sccache: 'true'` and let `setup-rust` publish the
proxy credentials. On a GitHub-hosted runner the action selects GitHub's
service; if you prefer the local-disk arm described under
[Rust cache ownership](#rust-cache-ownership), which measured better for Rust
there, opt out as that section says.

### Reserved `ACTIONS_*` variables, once

The rule behind all of this is worth stating once, because it is not written
down anywhere in GitHub's documentation and it has now cost this estate two
issues, [#433](https://github.com/leynos/shared-actions/issues/433) and
[chutoro #244](https://github.com/leynos/chutoro/issues/244).

The runner hands `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` to
*JavaScript* action steps and to nothing else. Neither a workflow `run:` step
nor a composite action's `run:` step sees them, which is why
`export-ubicloud-cache-credentials` is a JavaScript action: it reads them where
they exist and republishes them through `GITHUB_ENV`, where a shell step can
read them.

What the runner does not do is take them back. Measured on
`ubicloud-standard-2`, a composite action's `run:` step and a workflow `run:`
step read exactly the same values at every position in a job, before and after
that export, and a server started from either bound the proxy. Nothing is
re-injected or overridden.

So when a value published through `GITHUB_ENV` turns out wrong later in a job,
the cause is a step that wrote `GITHUB_ENV`, not the runner. Any JavaScript
action can do that with `core.exportVariable`, and one that touches a reserved
`ACTIONS_*` name will silently outrank whatever a caller exported earlier. Look
for the write before assuming the platform.

## Rust cache ownership

`setup-rust` and `generate-coverage` accept `cache-provider: github` or
`cache-provider: external`. Any other value fails before a cache step runs. The
default `github` mode preserves the actions' Cargo and uv GitHub caches;
setup-uv remains automatic, so its cache is enabled on GitHub-hosted runners
and disabled on self-hosted runners. Those Cargo caches archive the registry,
the Git index, and (for coverage) the installed Cargo binaries. The `target`
tree is deliberately uncached in both modes: sccache owns compiler output.

Use `external` when the calling workflow mounts the same Cargo or uv paths
through another service such as a Namespace cache volume. The action then skips
its overlapping GitHub archive caches; it does not mount or report the external
cache itself. Mount the external cache before installing dependencies or
building, and report its own cache-hit signal in the caller.

The `use-sccache` input is independent. When the external service owns a local
sccache directory, set `use-sccache: 'false'`, install a trusted prebuilt
sccache binary, set `RUSTC_WRAPPER=sccache`, and mount that directory
explicitly. Otherwise the shared action would still select its GitHub-backed
compiler-cache integration even though its Cargo and uv archive caches were
disabled.

### Which sccache backend for which runner

On Ubicloud, use the proxy, which `setup-rust` selects by default. The proxy is
on the runner's own private network, so a hit costs almost nothing.

On a GitHub-hosted runner `setup-rust` selects GitHub's service, but prefer the
local-disk arm for Rust. The GitHub Actions backend measured 0.28 s per cache
hit against 0.42 s per compile on Chutoro, which spends most of what it saves,
and Whitaker's Windows lane had every one of 643 writes rejected. The same lane
against a local directory under `actions/cache` recorded no read errors, no
write errors and a 78 % warm hit rate. So on those runners:

- set `use-sccache: 'false'`, which turns off both the installation and the
  server, and install a pinned, checksum-verified sccache yourself;
- point `SCCACHE_DIR` at a directory inside the workspace and export
  `RUSTC_WRAPPER`;
- restore that directory on pull requests with
  `actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9`, the v6.1.0 pin;
- let one designated job save it on a push to `main`, so the readers and the
  writer do not contend for the key.

The install is the lane's own, and pinned by version and digest, because
nothing here is built from source or fetched unverified. This one is for
**Linux x86_64 runners**, and only those: it names the
`x86_64-unknown-linux-musl` archive, and `sha256sum` is a GNU coreutils tool
that macOS does not ship. A macOS lane takes the `aarch64-apple-darwin` or
`x86_64-apple-darwin` archive and `shasum -a 256 -c`, and a Windows lane the
`x86_64-pc-windows-msvc` zip. Each has its own digest, published beside the
archive as a `.sha256` file.

```yaml
- name: Install sccache
  shell: bash
  env:
    SCCACHE_VERSION: v0.17.0
    SCCACHE_SHA256: >-
      67c4a96dd237c1f518f6b36083f270f9976d516f1e57fce891755ea782e50006
  run: |
    set -euo pipefail
    archive="sccache-${SCCACHE_VERSION}-x86_64-unknown-linux-musl.tar.gz"
    url="https://github.com/mozilla/sccache/releases/download"
    curl -fsSL -o "$archive" "${url}/${SCCACHE_VERSION}/${archive}"
    echo "${SCCACHE_SHA256}  ${archive}" | sha256sum -c -
    tar -xzf "$archive"
    install -Dm755 "${archive%.tar.gz}/sccache" "${HOME}/.local/bin/sccache"
    echo "${HOME}/.local/bin" >> "$GITHUB_PATH"
```

This pin is the lane's, and independent of the one `setup-rust` uses for
`mozilla-actions/sccache-action`. Across this estate the two do not currently
agree: Whitaker and Axinite pin 0.16.0, Chutoro and Wildside 0.17.0.

`rust-build-release` accepts both inputs too and forwards them to the
`setup-rust` step it runs internally, so a workflow that only calls the build
action can still name the cache owner, and inherits the sccache behaviour
described above along with it. It caches nothing itself, and rejects an
unrecognized `cache-provider` before installing a toolchain.

The coverage action's ratchet baseline is cached separately from all of this.
Its restore step uses `actions/cache/restore` and its save step uses
`actions/cache/save`, both at the same pinned revision, keyed by the run id.
Only the save step writes, so the two halves no longer contend for the key.
That pair reports its own bounded `hit`, `miss`, `skipped`, `disabled`, or
`error` restore state and `saved`, `skipped`, `disabled`, or `error` save
state, naming neither the key nor the baseline paths. Each outcome is also
written as a fixed `metric ratchet-cache.restore=<state>` or
`metric ratchet-cache.save=<state>` line, so a log scraper can read the result
without parsing the notice text. `disabled` means the ratchet is off; `skipped`
means an earlier failure stopped the step running.

### When the baseline is published

In the default `auto` mode the save publishes only on a `push` to
`refs/heads/main`. A `workflow_dispatch` never publishes, and neither does a
push to any other branch. `publish-baseline: always` lifts both restrictions.

That matters for two reasons. A dispatch is how warm-cache evidence is
gathered, by re-running the same workflow over an unchanged tree; a run that
publishes disturbs the generation it was measuring, and adds an entry rather
than replacing one, since the key names the run. And a push to a branch other
than the trunk would advance the baseline that later pull requests are measured
against, which is a correctness question rather than housekeeping.

A repository whose merges fire no `push` event, because they land through an
automerge token, and one whose trunk is not called `main`, set
`publish-baseline: always`. The calling workflow is then responsible for
restricting the job to the runs that should publish, since the action no longer
does. It defaults to `auto`, the guarded behaviour, so no caller inherits the
exception.

`ratchet-coverage` carries the same input and the same guard.

## `ratchet-coverage` baseline caching

`ratchet-coverage` stores its coverage baseline in a GitHub cache between runs.
That cache used one constant key, `ratchet-baseline-<os>`. Cache entries are
immutable, so the key could only ever be written once and then held whatever
the first run after an eviction measured, which made later runs report a
decrease that had not happened.

The baseline is now keyed per run, `ratchet-baseline-<os>-<run id>`, restored
through the shared `ratchet-baseline-<os>-` prefix so each run recovers the
newest stored baseline. Nothing changes in how the action is called, and no
input moves. Existing entries under the old key are simply never read again;
the first run after this change starts from no baseline and stores one.

## Running coverage as the only test execution

`generate-coverage` accepts `all-features`, `all-targets`, and `doctests` so a
repository can make the coverage job its entire test run rather than executing
the suite twice. All three default to off, and the ratchet cache change above
is internal to the action, so a workflow already pinned to `v1` needs no edit
to keep its current behaviour. Opt in only when the coverage job must replace a
separate test job.

```yaml
- uses: leynos/shared-actions/.github/actions/generate-coverage@v1
  with:
    output-path: coverage.xml
    all-features: 'true'
    all-targets: 'true'
    doctests: 'true'
```

`all-features` supersedes `with-default-features` and is rejected alongside a
non-empty `features` list, because it already enables everything that list
could name. `all-targets` covers benches, examples, and every test target. Doc
tests are a separate Cargo target kind that `cargo llvm-cov`'s nextest path
cannot execute, so `doctests` runs them afterwards through
`cargo test --doc --workspace` with the same feature selection; that run is
uninstrumented and contributes no coverage. A `RUSTFLAGS` value the workflow
exports, such as `-D warnings`, applies to every Cargo invocation the action
makes.

Both actions report bounded `hit`, `miss`, `disabled`, or `error` states for
their own archive caches in the workflow log and job summary. Coverage ratchet
baseline files remain separate GitHub caches when external mode does not mount
their paths.

## `export-ubicloud-cache-credentials` action

Use this action on **Ubicloud runners only**, to make Ubicloud's cache proxy
reachable from shell steps.

A GitHub Actions runner exposes `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN`
to JavaScript action steps alone. Neither a workflow `run:` step nor a
composite action's `run:` step sees them, so a shell step that starts an
sccache server cannot learn where the cache lives. On Ubicloud those values
name a proxy on the runner's private network that stores objects in Ubicloud's
cache rather than GitHub's. The action reads them where they are visible and
republishes them through `GITHUB_ENV`.

It also exports `ACTIONS_CACHE_SERVICE_V2` empty. sccache's GitHub Actions
backend selects the v2 cache service whenever that variable is set, and the
proxy serves v1.

Run it before `setup-rust`, and leave `use-sccache: 'true'`. The two work
together now. `setup-rust` runs `mozilla-actions/sccache-action`, whose last
act is to write `ACTIONS_CACHE_SERVICE_V2=on` to `GITHUB_ENV`, together with
`ACTIONS_RESULTS_URL` and `ACTIONS_RUNTIME_TOKEN`. That undid the empty value
this action publishes, so every later step selected GitHub's v2 results service
and the first one to start a server bound it; Chutoro and Wildside both landed
zero objects in Ubicloud's store that way. `setup-rust` now records your value
before those steps and restores it after them, and starts the sccache server
itself once the restore is in effect. That was
[#441](https://github.com/leynos/shared-actions/issues/441).

The other two of those three exports are harmless here. On Ubicloud the runner
already gives JavaScript action steps the proxy's own `ACTIONS_CACHE_URL` and
`ACTIONS_RUNTIME_TOKEN`, which is how this action reads them, and
`ACTIONS_RESULTS_URL` is unset throughout. Only the v2 flag did damage.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: leynos/shared-actions/.github/actions/export-ubicloud-cache-credentials@v1
- uses: leynos/shared-actions/.github/actions/setup-rust@v1
  with:
    use-sccache: 'true'
```

A lane that would rather own sccache outright can still set
`use-sccache: 'false'`, which turns off the installation as well as the server.
Nothing on the Ubicloud image provides the binary, and `RUSTC_WRAPPER=sccache`
naming a binary that is not on `PATH` fails every `cargo` invocation rather
than falling back to an uncached build, so such a lane installs a pinned,
checksum-verified release archive itself, exports the wrapper and the backend,
and starts the server in a step after those exports, because `GITHUB_ENV`
reaches only the next step.

Order matters either way, and for one reason: sccache reads its cache
configuration once when the server starts and keeps it for that server's life.
So the credentials must be published before anything starts a server, which
includes `setup-rust`.

The action fails the step when the cache URL or runtime token is absent, when
the URL does not parse, or when its host is neither `localhost` nor a
private-network address literal. `localhost` is the one name accepted; any
other host must be a complete IPv4 or IPv6 literal, so a DNS name that merely
begins with a private octet, or that would resolve to a private address, is
refused. A public host means the variable points at GitHub's own endpoint, so
this is not an Ubicloud runner and the action must not be used there. Both the
token and the URL are masked before anything is logged, because the URL's path
segment is bearer-like, and the single notice names the proxy's host and port
only. Each run reports one bounded
`metric ubicloud-cache-credentials.result=<state>` line, over `exported`,
`missing-cache-url`, `missing-runtime-token`, `invalid-url`, and `public-host`.

## `install-tool` action

Installs one pinned, digest-verified tool from
[`.github/tool-manifest.toml`](../.github/tool-manifest.toml):

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: ./.github/actions/install-tool
  with:
    tool: cargo-nextest
    version: 0.9.143
```

From another repository, reference it by commit SHA rather than by a moving
tag, so the action code cannot change without the reference changing.

`version` is required and must name a manifest entry. There is no `latest`, and
a version the manifest does not carry fails closed rather than reaching for
whatever is newest. A newer version belongs in the manifest; making it float is
not the fix. An unpinned lookup is a network call in the critical path and a
dependency that changes underneath the lane, which is how a job on
[#440](https://github.com/leynos/shared-actions/pull/440) died with
`Unable to locate executable file: undefined`.

The manifest currently carries cargo-audit, cargo-nextest, cargo-llvm-cov,
cargo-dylint, dylint-link and sccache.

**Every pinned digest was computed here from an independent download of the
archive it describes. That is the trust anchor.** Where upstream publishes a
`.sha256` sidecar it was fetched and compared, and the entry records the result
in `sidecar-verified`, over `true`, `absent` when upstream publishes none, and
`false` when one exists and could not be read. The sidecar is a cross-check on
the pin's provenance and never the source of it: copying a digest out of one
records only that the sidecar agrees with itself.

Two limits are worth knowing before planning around it.

**Dylint publishes Linux archives only**, so `cargo-dylint` and `dylint-link`
fail closed on macOS and Windows, and a lane running Dylint there still has no
prebuilt option and builds from source. Asked for upstream in
[trailofbits/dylint#2068](https://github.com/trailofbits/dylint/issues/2068).
Every Tier 2b repository runs Dylint on `ubuntu-latest` today, so nothing is
blocked; this is here so that a lane moving off Linux discovers it now rather
than in a red gate.

**cargo-audit and cargo-llvm-cov publish no digest sidecars at all**, for any
target, so their entries carry `sidecar-verified = "absent"`. What is missing
is the corroboration of the pin, not any check CI performs, because the digest
is still verified on every download.

By default, the binary goes to `~/.cargo/bin`, and `bin-dir` moves it. Either
way the directory is added to `PATH`, so the next step calls the tool by name.
`bin-dir` must be absolute, since a relative one would make the `path` output
relative despite its name and resolve differently in a step with another
working directory. On Windows, `path` is the native form and `posix-path` the
one a `bash` step wants.

**The action archives nothing.** It installs, and a lane that wants an
installed tool to survive between jobs owns that cache step and its key,
exactly as it owns the sccache directory under the local-disk arm. What the
action does do is probe: a call that finds the exact version already present
skips the download and reports `cache-hit: true`. A binary of the right name
and the wrong version is a miss, because that is the failure worth catching.

Each run emits bounded `metric install-tool.*` lines covering resolution, the
cache probe, the download, the digest, the install and the version check. The
[action's README](../.github/actions/install-tool/README.md) lists the closed
set each ranges over.

## `install-whitaker` action

The `install-whitaker` composite action downloads a verified prebuilt
`whitaker-installer` from the requested Whitaker GitHub release and runs it to
install the Dylint suite. It never builds the installer from source. Use the
action from a workflow in this repository with its local path:

```yaml
- name: Check out the repository
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

- name: Install Whitaker
  uses: ./.github/actions/install-whitaker
```

The repository must be checked out before invoking this local action.

**The action pins the installer, not the lints it installs.** By default the
installer builds the Dylint suite from the Whitaker default branch tip, so a
change on that branch alters lint results with no commit in the consuming
repository. The optional `suite-version` input names a tag, branch or commit to
build from instead, so a suite change arrives as a reviewed bump:

```yaml
- name: Install Whitaker
  uses: ./.github/actions/install-whitaker
  with:
    suite-version: v0.2.8
```

A pin costs a source build, because prebuilt lint libraries are published only
for the branch tip, and it needs installer 0.2.8 or later. The action's
`ci-mode` input defaults to on and rejects a pin for that reason: CI pins the
installer and consumes the published binaries, and a lane that wants the cost
must say so with `allow-suite-pin: true`. `ci-mode` also verifies the rolling
assets before the installer runs, retrying a short absence, and fails the step
if the installer resorted to a source build anyway.

Every run records which path it took as
`whitaker-installer.suite-source=<prebuilt|source>`, and the toolchain the
published libraries were built with as
`whitaker-installer.suite-toolchain=<toolchain>`, so a lint result can be tied
to the compiler that produced it. Set `ci-mode: false` when a source build is
the intention, such as reproducing a lint locally against a modified suite. The
path is still recorded, but `source` no longer fails the step.

A pin cannot be applied when the workflow runs inside a Whitaker checkout
because checking out a reference there would move the working tree the run is
using. Each run records which arm it took as
`whitaker-installer.suite=<pinned-commit|pinned-mutable-ref|default-branch-tip>`.
Only a full commit identifier reports as `pinned-commit`; a branch or tag is
reported as mutable because it can move without the caller changing anything.

The optional `installer-version` input selects the `whitaker-installer` version
and defaults to `0.2.8`. The optional `cargo-home` input defaults to
`~/.cargo`; it controls the cached `whitaker-installer` location
(`${{ steps.validate-inputs.outputs.installer-path }}`). The optional
`cache-provider` input defaults to `github`; use `external` when the caller
mounts this path and the installed suite through a Namespace cache volume.

In `github` mode, the action restores the installer and
`~/.local/share/whitaker` using a key containing the runner operating system,
architecture, installer version, `dylint.toml` hash, and effective expanded
Cargo home. A hit reuses both tool layers. On a miss, the action verifies the
release archive against a SHA-256 digest pinned in the action itself before
extracting its executable. A missing prebuilt asset is a hard failure.

The pinned digests live in `installer-digests.sha256` beside the action
manifest, so a compromised release cannot satisfy its own check by publishing a
matching `.sha256` sidecar. The sidecar is still fetched and must agree with
the verified archive.

Digests are computed in a way that does not depend on what the file is called.
`sha256sum` escapes its output line when the name it was given contains a
backslash or a newline, and a Windows runner's Git Bash paths contain
backslashes, so reading the digest back out of that line used to reject an
intact archive. The action now hashes the archive from standard input, where no
name appears, and strips a leading backslash from a digest read out of a
release sidecar. Windows lanes verify correctly with no caller change.

On Windows runners the staging directory comes from `RUNNER_TEMP`, which is a
native path, so under Git Bash it carries a drive letter. The action converts
it to POSIX form before use, because GNU tar reads a colon in an archive path
as remote `host:path` syntax and would try to resolve the drive letter as a
hostname. GNU tar is additionally told to treat a colon literally. Neither
affects a caller: no input changes, and Linux and macOS runners are untouched.

The pinned manifest takes precedence. Pass the optional `installer-sha256`
input only for an asset the manifest does not pin; a digest that disagrees with
a pinned one is rejected, and an asset with neither anchor fails before
anything is downloaded.

For an external cache, mount `~/.local/share`, not the terminal
`~/.local/share/whitaker` directory. The installer expects that child to be
absent for a fresh install; an empty volume mounted at the child looks like an
existing but invalid Git checkout.

## `install-mdtablefix` action

The `install-mdtablefix` composite action installs a pinned `mdtablefix` from
its prebuilt release through a hardened `cargo binstall`. It never builds the
tool from source. Every repository whose `make check-fmt` shells out to
`mdtablefix` should use it instead of its own installer step.

```yaml
- name: Check out the repository
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

- name: Install mdtablefix
  uses: ./.github/actions/install-mdtablefix
  with:
    version: 0.5.0
```

The repository must be checked out before invoking this local action. The
required `version` input names the exact release. The optional
`binstall-version` input selects the `cargo-binstall` release installed when
the runner has none and defaults to `1.22.0`. The optional `bin-dir` input
defaults to `~/.local/bin`; the executable lands there and the directory is
appended to `GITHUB_PATH`, so later steps call `mdtablefix` by name.

### The two-step cache pattern

The action caches nothing itself, so one key keeps one owner. The workflow
restores `bin-dir` and then invokes the action. A directory that already holds
the pinned version short-circuits the install and reports
`install-mdtablefix.result=cached`.

```yaml
- name: Restore mdtablefix
  uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
  with:
    path: ${{ runner.temp }}/mdtablefix-bin
    key: mdtablefix-0.5.0-${{ runner.os }}-${{ runner.arch }}

- name: Install mdtablefix
  uses: ./.github/actions/install-mdtablefix
  with:
    version: 0.5.0
    bin-dir: ${{ runner.temp }}/mdtablefix-bin
```

Save that cache from one designated writer, on push to the default branch,
exactly as for every other tool cache in the estate.

### Platform support

`mdtablefix` 0.5.0 publishes prebuilt archives for Linux gnu on `x86_64` and
`aarch64` only. macOS and Windows have no asset at all, so the action fails
closed there with `install-mdtablefix.result=no-prebuilt` rather than
compiling. A workflow with a macOS or Windows formatter lane keeps its own
documented source-build exception until `mdtablefix` publishes assets for that
platform. What unblocks those platforms is
[leynos/mdtablefix#459](https://github.com/leynos/mdtablefix/issues/459), which
asks for macOS and Windows release assets and for the `binstall` metadata to be
ungated from `linux-gnu`. The action's `no-prebuilt` annotation names that
issue, so a failing run says what would fix it.

### Reported outcomes

Each run writes exactly one `install-mdtablefix.result` line to the job
summary, over `invalid-input`, `cached`, `installed`, `no-prebuilt`,
`binstall-unavailable`, `install-failed`, and `version-mismatch`, plus at most
one `install-mdtablefix.binstall` line over `present` and `installed`. A
failure is also annotated with `::error`.

## `generate-coverage` action

The `generate-coverage` composite action runs `cargo llvm-cov` for Rust
projects and, by default, drives it through `cargo nextest`.

`cargo-llvm-cov` itself is installed from the repository's tool manifest
(`.github/tool-manifest.toml`), at 0.9.0. The installer selects the archive for
the runner's operating system and architecture (x86_64 and aarch64 Linux,
x86_64 and aarch64 macOS, x86_64 Windows), verifies its SHA-256 digest against
the manifest, extracts only the named executable into `CARGO_HOME/bin`, and
reuses an installed binary that already reports the pinned version. There is no
`cargo binstall` or `cargo install` fallback. 0.9.0 is the first release that
reads Cargo's new build-dir layout, which cargo 1.100 nightlies enable by
default; the previous 0.6.24 searched `target/llvm-cov-target/debug/deps` and
failed with `failed to collect object files` on those toolchains after every
test had passed. The same installer serves `ratchet-coverage`. The optional
`use-cargo-nextest` input defaults to `true`; set it to `false` to run
`cargo llvm-cov` directly instead of through `cargo nextest`.

When `use-cargo-nextest` is enabled, the action installs `cargo-nextest` by
downloading the pinned official release archive published by the
`nextest-rs/nextest` GitHub project. It never builds `cargo-nextest` from
source, and there is no `cargo install` fallback of any kind. The pinned
release is 0.9.145.

That pin fixes a warning flood under Cargo's new build-directory layout. Older
`cargo-nextest` releases replayed a build script's `cargo::rustc-env`
directives by opening `<out_dir>/../output` by hand, which depends on Cargo's
internal build-directory layout, so every workspace package with a build script
produced a repeated
`warning: could not find build script output file at …/build/<hash>/output`.
From 0.9.131 onward, `cargo-nextest` reads those directives from the structured
`build_script_info` that Cargo reports in its own JSON build messages instead,
and only falls back to the raw-file parse for a nextest archive written by an
older release. This action always runs a live `cargo llvm-cov nextest` build
rather than a pre-built nextest archive, so it never takes that fallback and
the warnings stop.

Before installation, the action verifies two separate SHA-256 digests: one for
the downloaded release archive, and a second for the executable that is
extracted from it. Both digests are pinned in the installer script alongside
the pinned `cargo-nextest` release version. Installation only proceeds once
both checks pass.

The action selects the release archive using the runner's operating system and
architecture:

- Linux x86_64, split into a `gnu` and a `musl` archive. The installer
  detects musl by probing the runner's libc through `ctypes.CDLL` and checking
  whether `gnu_get_libc_version` is present; its absence indicates musl.
- Linux aarch64, `gnu` only.
- macOS, a single universal archive covering both architectures.
- Windows x86_64 and Windows aarch64.

An unsupported platform, a failed download, or a digest mismatch -- for either
the archive or the extracted executable -- is a hard failure: the installer
exits non-zero and the action stops. There is no fallback to
`cargo install cargo-nextest` in any of these cases.

### Python coverage source scope

The action also accepts an optional `python-source` input, which defaults to
empty. An unset, empty, or whitespace-only value leaves Slipcover's automatic
source discovery in place, so existing callers are unaffected. A non-empty
value is passed through unchanged as a single `--source` argument, placed
immediately before Slipcover's `--branch` flag, so a comma-separated scope such
as `episodic,alembic` reaches Slipcover as one value rather than several paths.
Two scopes in use in the estate are `episodic,alembic` and `./lading`.

The scope is an inclusion boundary. By default Slipcover instruments files
under the working directory and skips the standard library and the running
interpreter's own `site-packages`. A second virtual environment inside the
repository, such as a project `.venv` that is not the one running the tests, is
under the working directory but is not the running interpreter's, so its
dependencies are instrumented, land in the report, and move the percentage for
reasons unrelated to the project. Naming the project's directories keeps that
foreign `site-packages` out of the number. Every entry must also be
repository-relative, as described next.

A non-empty value is validated before any coverage work begins, and the action
fails rather than measuring something other than what was asked for. An empty
entry such as `femtologging,,generated` is refused, and so is an entry with
surrounding whitespace, since `femtologging, generated` names a directory whose
name begins with a space, which Slipcover would never find: write the list
without spaces around the commas. Every entry must resolve inside the
repository: an absolute path, even one naming a directory inside the
repository, a path that escapes through `..`, and a symbolic link that resolves
outside the repository are each rejected, and the error names the offending
entries.

```yaml
- name: Generate coverage
  uses: ./.github/actions/generate-coverage
  with:
    language: python
    output-path: coverage.xml
    python-source: episodic,alembic
```

Separately, the action prepends the coverage environment's scripts directory --
the directory containing the `.venv-coverage` interpreter -- to `PATH` for the
coverage process, keeping the inherited `PATH` after it. An executable that a
test creates and then runs therefore resolves against the coverage environment
and can import the project development dependencies that `uv sync` installed
there.

## Test timeouts: four tiers, outermost last

Four independent timers can end a coverage run, and a caller sets them in four
different places. A run that dies without an obvious cause is nearly always one
of them.

| Tier                     | What it bounds                     | Where it is set                                        |
| ------------------------ | ---------------------------------- | ------------------------------------------------------ |
| Per-test `slow-timeout`  | one test                           | `.config/nextest.toml`                                 |
| nextest `global-timeout` | the whole test run                 | `.config/nextest.toml`                                 |
| Cargo watchdog           | one `cargo` invocation, wall clock | `cargo-wait-timeout`, or `RUN_RUST_CARGO_WAIT_TIMEOUT` |
| Job `timeout-minutes`    | the whole job                      | the job holding the coverage step                      |

The watchdog belongs to `generate-coverage` and defaults to 1,800 seconds.
Nothing in a caller's nextest configuration mentions it, which is how it comes
to sit underneath a larger nextest budget without anyone noticing.

`RUN_RUST_CARGO_WAIT_TIMEOUT` takes precedence over the `cargo-wait-timeout`
input, and the 1,800-second default applies only when neither is set. A blank
value counts as unset. The step prints the budget it chose before `cargo`
starts. It refuses a value that is not a number, is not finite, or is zero or
negative, before `cargo` starts. The error names the setting and quotes the
rejected value:

```text
::error::RUN_RUST_CARGO_WAIT_TIMEOUT must be a finite number of seconds greater than zero; got '0'
```

A value that is not a number at all now has its rejected value quoted too.
Nothing else about the check changed, so callers need to change nothing.

If a repository has set no value it is on 1,800 seconds and nothing in the
repository says so. That is not a budget anybody chose, and it is the state
that killed a dependency bump in `rstest-bdd` with 1,894 of its 1,897 tests
complete.

### Choosing a value

1. **Read several recent runs, not one, and not only the green ones.** Take
   the longest the coverage step has taken and note the run ids. One run is the
   coldest seen so far rather than a measurement of the cold case. Include runs
   a timeout ended: those are the strongest evidence an allowance was too
   small, and sizing from the successful ones alone reproduces the failure they
   recorded.
2. **Add what the watchdog covers and the inner timers do not.** Under nextest
   that is its `global-timeout`, plus a termination allowance, plus a cold
   build, plus the report phase that follows the test run. Without nextest, the
   observed step duration is the whole of it.
3. **Set it where the whole job can see it**, as
   `RUN_RUST_CARGO_WAIT_TIMEOUT` at job level or `cargo-wait-timeout` on the
   step, and record beside it what it was sized against.
4. **Check the job ceiling above it**, or the job is cancelled before the
   watchdog can report the overrun and the log that would have explained it is
   discarded.

### The arithmetic

The four clocks do not start together, so comparing the configured numbers is
not enough. The watchdog starts with `cargo` and covers the build; nextest's
global timeout starts only when tests begin; hitting that timeout starts a
termination procedure rather than stopping the run instantly; and the job timer
runs from job start through everything either side of coverage.

```text
termination allowance =  configured slow-timeout.grace-period
                         on Linux and macOS
                         0 on Windows

watchdog              >= global-timeout
                         + termination allowance
                         + termination safety margin
                         + cold-build allowance
                         + report-phase allowance

job ceiling           >  sum of every watchdog window in that job
                         + measured work outside those windows
                         + a stated margin above that sum
```

Six details of that arithmetic are easy to read past, and each has been got
wrong in this estate.

- **The job ceiling contains a sum, not a multiple.** Each invocation of the
  action gets its own watchdog, so a job running coverage twice for two feature
  sets can legitimately spend both, and the two need not agree.
- **The sum is of watchdog windows, not of coverage steps.** A single step can
  arm the watchdog more than once, because `doctests: 'true'` runs
  `cargo llvm-cov nextest` and then an uninstrumented
  `cargo test --doc --workspace`, and each is a separate `cargo` invocation
  with its own watchdog window. A doctest-enabled coverage step therefore has
  two watchdog windows, and a job running it must contain both. The doctest
  pass is one of two optional invocations rather than the only one:
  `with-cucumber-rs`, together with a non-empty `cucumber-rs-features`, runs
  the cucumber.rs scenarios under coverage in a follow-up invocation of its
  own. So a step asking for both arms *three* windows and one asking for
  neither arms one, and the count is one per `cargo` invocation the step causes
  rather than a fixed number.
- **The report-phase allowance is not the termination safety margin.** The
  report-phase allowance covers what `cargo llvm-cov` spends after nextest
  stops, merging profile data and writing the report out. It is reached after a
  *normal* test run. The termination safety margin covers nextest's own
  teardown and report writing after a *timeout cancellation*. A run can do one,
  the other, or both, so neither term subsumes the other and both belong in the
  sum.
- **The comparison is strict, by a margin stated in the workflow.** The
  ceiling must exceed the sum it contains, not merely reach it. A ceiling equal
  to that sum cancels the job at the moment the watchdog would have reported
  the overrun, which converts a legible failure into a cancellation with no
  log. This estate carries fifteen minutes.
- **The termination allowance is platform-dependent, and is two terms.**
  nextest's own grace-period default is ten seconds, but configurations
  generated from this estate's template carry five, so read the value. On
  Windows termination is immediate and the term is zero. Any floor added on top
  is a safety margin against a grace period nobody has read, not time nextest
  is known to need, so keep it separate.
- **The per-test budget is `period` multiplied by `terminate-after`.** A
  `{ period = "60s", terminate-after = 10 }` allows ten minutes, not one.
  Reading the period alone understated netsuke's largest allowance tenfold.

Measure the outside allowance per lane rather than once. A pull-request lane
and a trunk lane can differ by an order of magnitude in what they do around the
coverage step, so give each its own figure with its own run id, and hold a lane
nobody has measured to the largest until somebody does.

State both allowances and where they were measured. `generate-coverage`'s
README carries the rest: the exact text the watchdog prints, the precondition
that no `cargo` runs without a root manifest, and the shape of the contract
that asserts the ordering by value.

### What the report phase measures

The report phase is the gap between nextest's last test and its `Summary` line,
during which `cargo llvm-cov` merges the profile data it collected and writes
the report out. The `Summary` line is where the interval ends, not the
`Finished report saved to lcov.info` message printed after it: those two are
adjacent lines in the log, a millisecond or so apart, because nextest reports
the run and the message is printed almost immediately. The work being measured
happens before the summary, while nextest's own clock has already stopped, and
the summary's own figure is what dates the end of the test run. Measuring to
the later message would report about zero. Netsuke measured the interval by
reading it out of coverage-step logs, in three runs whose lanes carry
`doctests: 'true'`:

| Run         | Lane                | Report phase |
| ----------- | ------------------- | ------------ |
| 34914144521 | `ci.yml`            | 274 s        |
| 34897199699 | `ci.yml`            | 91 s         |
| 34920593593 | `coverage-main.yml` | 86 s         |

*Table: the report phase measured on three Netsuke runs. The worst is 274 s,
and an allowance is sized above it rather than at it, because each of those
runs measured a warm profile merge and none measured the cold case.*

### The Netsuke example

Netsuke's coverage lanes exercise every correction above at once, so they are
worth reading as a worked example. Both lanes pass `doctests: 'true'`, and
`[profile.ci]` sets `global-timeout = "13m"`, which is 780 s.

```text
watchdog requirement  =  780 s + 70 s + 600 s + 300 s
                      =  1,750 s

job ceiling           >  2 x 1,800 s + 900 s + 900 s
                      =  5,400 s, so above 90 minutes
```

The first sum is the five-term requirement: the whole-run budget, the
termination allowance (nextest's ten-second default grace period plus a 60 s
safety margin, since its configuration names no grace period), a 600 s
cold-build allowance, and a 300 s report-phase allowance. At 1,750 s it sits
below the 1,800 s watchdog the lanes carry. The second is the ceiling
requirement for a doctest-enabled coverage step: **two** 1,800 s watchdog
windows rather than one, 900 s of measured work outside them, and a 900 s
margin above that sum. That last line carries `>` rather than `=`, and the
difference is the point of the section: 5,400 s is the smallest sum a ceiling
must exceed, not a ceiling that satisfies it, so a job holding such a step
declares more than 90 minutes. Counting one window per coverage step gives
3,600 s and a ceiling equal to the sum, which is the inversion the strict
comparison rejects.

Both allowances are conservative, and neither is a value Netsuke declares. The
600 s cold-build allowance is for a compiler cache none of the sampled runs
had, so it is a figure the sizing chose rather than one a run measured; the
largest per-test allowance Netsuke actually declares is 420 s, and 600 s
appears only in synthetic fixtures. The 900 s outside allowance and the 900 s
margin are likewise sizing decisions, stated here rather than read from a
configuration file.

## Mutation testing and workspace shape

`cargo mutants` selects packages the way Cargo does. When started in a
workspace root that has a root package, it mutates that package and no other
member unless `--workspace` or another package selector is given. So a scoped
run over a member's files enumerates nothing while reporting success. A caller
in that shape passes `--workspace` through `extra-args`.

A virtual workspace, one with no root package, already mutates every member, so
the same flag changes nothing there. Check which shape a repository has before
applying the fix; adding the flag uniformly leads to concluding it did nothing.

A day whose only Rust changes are test files is a separate matter and needs no
caller change: `mutation-cargo.yml` drops the files cargo-mutants cannot act on
before scoping, and skips the job rather than running it to a failure.

## The problem

The nested `actions-rust-lang/setup-rust-toolchain` action exports
`RUSTFLAGS="-D warnings"` whenever `RUSTFLAGS` is unset. An ambient `RUSTFLAGS`
environment variable overrides Cargo's `build.rustflags` setting in
`.cargo/config.toml`. Together these two behaviours mean that a project whose
source tree needs specific compiler flags — the motivating case is a
`-Zpolonius=next` borrow-checker flag — silently loses them in every step after
toolchain setup, because the setup step's default (or an inherited value) takes
precedence over the project's own configuration.

Both actions expose a `rustflags` input so callers can control this behaviour
explicitly.

## `setup-rust`'s `rustflags` input

`setup-rust` forwards `rustflags` directly to the nested
`actions-rust-lang/setup-rust-toolchain` step, which exports it as `RUSTFLAGS`.

- The default is `-D warnings`, preserving the action's historical
  behaviour.
- Set it to extra flags to replace that default.
- Set it to the empty string to leave `RUSTFLAGS` unset, so an inherited
  value or the project's `build.rustflags` in `.cargo/config.toml` applies
  instead.

## `rust-build-release`'s `rustflags` input

`rust-build-release` pins its own nested `setup-rust` step. Its `rustflags`
input defaults to empty, meaning the environment is left untouched: the nested
`setup-rust` step still applies its own `-D warnings` default in that case.
Setting a value exports it as `RUSTFLAGS` in an "Export caller RUSTFLAGS" step
that runs *before* toolchain setup, so the nested `setup-rust-toolchain` step
defers to it and the build honours it.

## Precedence

`rust-build-release` never overwrites a `RUSTFLAGS` value the caller has
already exported: its export step checks whether the variable is set at all, so
an inherited value wins over the `rustflags` input even when that inherited
value is the empty string.

`setup-rust` has no such guard. It forwards `rustflags` to
`actions-rust-lang/setup-rust-toolchain` unconditionally, so what happens to an
inherited `RUSTFLAGS` is that action's decision, not this one's. Pass the empty
string to have it leave `RUSTFLAGS` alone.

## Usage examples

### `setup-rust`

Keep the default `-D warnings` behaviour (no input needed):

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- uses: ./.github/actions/setup-rust
  with:
    toolchain: stable
```

Pass an extra flag, replacing the default:

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- uses: ./.github/actions/setup-rust
  with:
    toolchain: nightly
    rustflags: "-D warnings -Zpolonius=next"
```

Defer to the project's `.cargo/config.toml`:

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- uses: ./.github/actions/setup-rust
  with:
    toolchain: stable
    rustflags: ""
```

### `rust-build-release`

Keep the default (environment untouched, `-D warnings` still applies via the
nested `setup-rust` step):

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
```

Pass an extra flag required by the source tree:

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    rustflags: "-Zpolonius=next"
```

## Action selection

- Use `setup-rust`'s `rustflags` input when calling that action directly.
- Use `rust-build-release`'s `rustflags` input when using the build action,
  which pins its own nested `setup-rust` step and exports the value before that
  step runs.

## Install Nixie

The `install-nixie` action installs pinned Nixie and a checksum-verified Merman
command-line interface (CLI) release for Mermaid validation. The runner must
already provide `uv` and `curl` on `PATH`. Linux and macOS runners must also
provide `shasum` and `tar`; Windows runners must provide Git Bash's `cygpath`
and `powershell.exe`.

To use the action from this repository, check out the repository before calling
the local action:

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- name: Install Nixie
  uses: ./.github/actions/install-nixie
```

To use the published action:

```yaml
- name: Install Nixie
  uses: leynos/shared-actions/.github/actions/install-nixie@a197301888920eb21fbbc7e7bb6cb0c6f3d81584
```

The action accepts three optional version inputs:

| Input            | Default | Purpose                            |
| ---------------- | ------- | ---------------------------------- |
| `nixie-version`  | `1.1.0` | Nixie CLI release                  |
| `merman-version` | `0.7.0` | Verified Merman CLI release        |
| `python-version` | `3.14`  | Python used by uv to install Nixie |

Override the Nixie and Python pins when validating another supported toolchain
combination:

```yaml
- name: Install Nixie
  uses: leynos/shared-actions/.github/actions/install-nixie@a197301888920eb21fbbc7e7bb6cb0c6f3d81584
  with:
    nixie-version: "1.2.0"
    python-version: "3.13"
```

Merman version `0.7.0` is the only supported `merman-version`: each supported
runner pair (`Linux/X64`, `macOS/X64`, `macOS/ARM64`, and `Windows/X64`) has a
named official release archive and a checksum embedded in the action. Other
Merman versions and platforms fail closed before download. The action stores
Merman in `${XDG_CACHE_HOME:-${HOME}/.cache}/merman/0.7.0/bin` (`.exe` on
Windows), verifies its executable digest before every reuse, and verifies the
official archive on a cache miss. Callers that persist tool caches must include
`~/.cache/merman`. The action never invokes Cargo or builds Merman from source.

Nixie uses ordinary `uv tool install` reconciliation. The action requests
`--force` only when the expected executable shim is absent afterwards, and does
not use `nixie --version` as a probe. After both executable checks succeed, the
action appends the Merman and Nixie binary directories to `GITHUB_PATH`. Later
workflow steps can therefore invoke `nixie` and `merman-cli` directly.

## CodeScene coverage checks

`upload-codescene-coverage` installs its default CLI from the committed
manifest, rather than from a moving `latest` alias. The current Linux x64 pin
is cs-coverage 1.0.101 build `ca2b95180eff32b5072e81f20d718d7b747650be`; its
official archive is verified against SHA-256
`067503dc646c58c2d62c3315b55541e60e0b6343ef6d898650a9e0ae7cde0929` before the
action extracts it. The exact version, platform, and digest form the cache key
with no restore fallback. A requested version outside the manifest, an
unsupported runner, or a conflicting caller checksum stops before installation.
There is no source-build fallback.

The [`upload-codescene-coverage` action][codescene-coverage-action] supports
`upload` mode for analysed branches and `check` mode for the pull-request
changed-line coverage gate. `check` mode needs `CS_ACCESS_TOKEN` on the
pull-request lane, so a repository that follows main-owned coverage (below)
does not use it. In `check` mode, check out the full history (`fetch-depth: 0`)
and provide the CodeScene `project-url`; the CLI uses the merge base to
evaluate the gate. For LCOV, the report path must end in `.info`.

The pull request's base must already have coverage uploaded to CodeScene. If
that baseline is unavailable, `cs-coverage` cannot evaluate the gate; the
action preserves the CLI's normal diagnostics, adds the uploaded-base
explanation for status 2, and preserves the CLI's original exit status. It does
not use verbose diagnostics because they can expose Authorization headers.

Checks for stacked pull requests are intentionally skipped. When the pull
request base is not the repository's default branch, the action emits a warning
and skips the remaining check-mode steps, including CLI installation, artefact
upload, and the coverage gate. This lets a stacked pull request pass without
claiming that its changed-line coverage was evaluated; rebase or merge it onto
the default branch before relying on the gate result.

### Main-owned coverage

Main-owned coverage (concordat rule CV-005) keeps CodeScene and its credential
off every lane a pull request can start. This repository follows it, and it is
the shape the actions are written for.

- Pull-request lanes run `generate-coverage` with `with-ratchet: 'true'` and
  `publish-artefact: 'false'`. They compare against the ratchet baseline the
  latest `main` push saved, keep the report local to the job, and hold no
  `CS_ACCESS_TOKEN`. A lane that is also started by a push to `main` becomes a
  second writer of the baseline; give pull-request lanes no such trigger.
- One workflow that runs on pushes to `main`, and on no pull request,
  regenerates the same coverage, saves the next baseline, and runs
  `upload-codescene-coverage` in `upload` mode. Keep `CS_ACCESS_TOKEN` out of
  every `env`, since the action is composite and would pass a step's `env` on.
  Add a check step with an `id` whose sole command is
  `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`,
  guard the upload on that output being `'true'` and on
  `github.ref == 'refs/heads/main'` (a `workflow_dispatch` run selects its own
  ref), and pass `access-token: ${{ secrets.CS_ACCESS_TOKEN }}` directly. Key
  the workflow's `concurrency` group on the ref alone, such as
  `coverage-main-${{ github.ref }}`, with `cancel-in-progress: false`: a
  cancelled run loses its upload and its baseline write, and one group keeps
  uploads from pushes and dispatches in commit order. A manual re-run of an
  older run republishes that commit's coverage until the next push.
- Both sides pass the same `language`, the same `python-source` scope, and the
  same baseline file name, or the pull-request comparison reads a baseline
  measuring a different population, or one nothing writes.

A pull-request lane may still use `upload-codescene-coverage` in `install`
mode, which downloads the pinned CLI and contacts nothing. Anything that reads
the project configuration, `check` included, belongs in a workflow that runs
only from `main`. In this repository that is `test-codescene-parser-proof.yml`,
which is dispatch-only and bound to `refs/heads/main`; run it by hand after
changing the action, its manifest or the pinned CLI version.

To migrate a repository that ran `check` on pull requests, remove the `check`
step and its `fetch-depth: 0`, and add the pull-request ratchet inputs and the
publisher described above. The section "This repository's coverage publication"
in the [developers' guide](developers-guide.md) describes this repository's own
contract, which asserts every point in this list.

[codescene-coverage-action]: ../.github/actions/upload-codescene-coverage/README.md

## Dependabot auto-merge eligibility

The Dependabot auto-merge reusable workflow arms GitHub's auto-merge on a
dependency bump so it lands without a human review. Deciding that a pull
request qualifies therefore has to answer more than "did Dependabot open it?".

Every commit on the branch must be Dependabot's, wherever the commit list can
be read. Opening a pull request is not the same as writing what is in it, and
once Dependabot has opened one, anything pushed to that branch would otherwise
merge under the same rule, unreviewed. A branch carrying a commit Dependabot
did not write skips with `automerge_reason=foreign-commit:<sha>`, and the run
logs a notice naming each such commit and its author.

The qualification matters: where the commit list cannot be read at all the
check does not run, and the paragraph below says what happens then. What the
workflow guarantees is not that every merged branch was audited, but that no
branch merges over a foreign commit the audit saw.

Two consequences are worth knowing before pushing a fix onto a Dependabot
branch:

- **A co-authored commit counts as the co-author's.** A commit Dependabot
  pushed but a person co-wrote carries that person's change, so it makes the
  branch ineligible. The remedy is to open that change as its own pull request
  with its own review.
- **Dependabot stops rebasing an edited branch.** Keeping it current against
  the base branch becomes a manual job from then on. `@dependabot recreate`
  still works, but it rebuilds the branch from scratch and discards everything
  pushed onto it.

An auto-merge request already armed when the foreign commit arrives is
withdrawn rather than left in place, because GitHub keeps such a request alive
across a push and it would otherwise merge that commit as soon as the required
checks passed. Those runs report `automerge_status=cancelled`.

Arming and merging both name the head the audit read, so a push landing between
the two makes GitHub refuse the operation rather than act on a head nobody
looked at. That push starts its own run, which audits the new head.

Where the commit list cannot be read at all, the check fails open: the run
proceeds on the author alone and logs a warning saying so, because a query
fault that halted every consumer's auto-merge at once is a worse failure than
the one this prevents. A single commit whose credited authors came back
truncated, or which credits nobody at all, is treated the other way, as
foreign, since a partial or absent credit list certifies nothing.

Every run logs `automerge_commit_audit=` with one of `clean`, `foreign` or
`unreadable`, so the outcome can be counted without reading the notices.

See the
[Dependabot auto-merge reusable workflow](./dependabot-automerge-workflow.md)
for the merge-state rules, the required repository settings, and the full
decision log, and
[Migrating to the audited Dependabot auto-merge](./migrating-to-audited-dependabot-automerge.md)
before pulling in the next major tag.
