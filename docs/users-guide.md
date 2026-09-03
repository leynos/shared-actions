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

`rust-build-release` accepts both inputs too and forwards them to the
`setup-rust` step it runs internally, so a workflow that only calls the build
action can still name the cache owner. It caches nothing itself, and rejects an
unrecognized `cache-provider` before installing a toolchain.

The coverage action's ratchet baseline is cached separately from all of this.
Its restore step uses `actions/cache/restore` and its save step uses
`actions/cache/save`, both at the same pinned revision, keyed by the run id.
Only the save step writes, so the two halves no longer contend for the key.
That pair reports its own bounded `hit`, `miss`, `skipped`, `disabled`, or
`error` restore state and `saved`, `skipped`, `disabled`, or `error` save
state, naming neither the key nor the baseline paths. `disabled` means the
ratchet is off; `skipped` means an earlier failure stopped the step running.

## Running coverage as the only test execution

`generate-coverage` accepts `all-features`, `all-targets`, and `doctests` so a
repository can make the coverage job its entire test run rather than executing
the suite twice. All three default to off, and the ratchet cache change above
is internal to the action, so a workflow already pinned to `v1` needs no edit
to keep its current behaviour. Opt in only when the coverage job must replace
a separate test job.

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

The optional `installer-version` input selects the `whitaker-installer` version
and defaults to `0.2.7`. The optional `cargo-home` input defaults to
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

The pinned manifest takes precedence. Pass the optional `installer-sha256`
input only for an asset the manifest does not pin; a digest that disagrees with
a pinned one is rejected, and an asset with neither anchor fails before
anything is downloaded.

For an external cache, mount `~/.local/share`, not the terminal
`~/.local/share/whitaker` directory. The installer expects that child to be
absent for a fresh install; an empty volume mounted at the child looks like an
existing but invalid Git checkout.

## `generate-coverage` action

The `generate-coverage` composite action runs `cargo llvm-cov` for Rust
projects and, by default, drives it through `cargo nextest`. The optional
`use-cargo-nextest` input defaults to `true`; set it to `false` to run
`cargo llvm-cov` directly instead of through `cargo nextest`.

When `use-cargo-nextest` is enabled, the action installs `cargo-nextest` by
downloading the pinned official release archive published by the
`nextest-rs/nextest` GitHub project. It never builds `cargo-nextest` from
source, and there is no `cargo install` fallback of any kind.

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

The [`upload-codescene-coverage` action][codescene-coverage-action] supports
`upload` mode for analysed branches and `check` mode for the pull-request
changed-line coverage gate. In `check` mode, check out the full history
(`fetch-depth: 0`) and provide the CodeScene `project-url`; the CLI uses the
merge base to evaluate the gate. For LCOV, the report path must end in `.info`.

The pull request's base must already have coverage uploaded to CodeScene. If
that baseline is unavailable, `cs-coverage` cannot evaluate the gate; the
action prints the CLI's verbose diagnostic, adds the uploaded-base explanation
for status 2, and preserves the CLI's original exit status.

Checks for stacked pull requests are intentionally skipped. When the pull
request base is not the repository's default branch, the action emits a warning
and skips the remaining check-mode steps, including CLI installation, artefact
upload, and the coverage gate. This lets a stacked pull request pass without
claiming that its changed-line coverage was evaluated; rebase or merge it onto
the default branch before relying on the gate result.

[codescene-coverage-action]: ../.github/actions/upload-codescene-coverage/README.md
