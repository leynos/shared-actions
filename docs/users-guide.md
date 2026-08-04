# Users' Guide: Rust Flags and CodeScene Coverage

This guide explains the `rustflags` inputs exposed by the `setup-rust` and
`rust-build-release` composite actions, and the CodeScene coverage modes
provided by `upload-codescene-coverage`. It covers why these inputs and modes
exist and how to configure them for common scenarios.

## Related documents

- [Rust Build and Release Pipeline design](./rust-build-release-pipeline.md)
  – see "3.1.3 Caller-Controlled `RUSTFLAGS`" for the underlying design
  rationale and implementation notes.
- [`setup-rust` README](../.github/actions/setup-rust/README.md) – full input
  and output tables.
- [`rust-build-release` README](../.github/actions/rust-build-release/README.md)
  – full input and output tables.

## The problem

The nested `actions-rust-lang/setup-rust-toolchain` action exports
`RUSTFLAGS="-D warnings"` whenever `RUSTFLAGS` is unset. An ambient
`RUSTFLAGS` environment variable overrides Cargo's `build.rustflags` setting
in `.cargo/config.toml`. Together these two behaviours mean that a project
whose source tree needs specific compiler flags — the motivating case is a
`-Zpolonius=next` borrow-checker flag — silently loses them in every step
after toolchain setup, because the setup step's default (or an inherited
value) takes precedence over the project's own configuration.

Both actions expose a `rustflags` input so callers can control this
behaviour explicitly.

## `setup-rust`'s `rustflags` input

`setup-rust` forwards `rustflags` directly to the nested
`actions-rust-lang/setup-rust-toolchain` step, which exports it as
`RUSTFLAGS`.

- The default is `-D warnings`, preserving the action's historical
  behaviour.
- Set it to extra flags to replace that default.
- Set it to the empty string to leave `RUSTFLAGS` unset, so an inherited
  value or the project's `build.rustflags` in `.cargo/config.toml` applies
  instead.

## `rust-build-release`'s `rustflags` input

`rust-build-release` pins its own nested `setup-rust` step. Its `rustflags`
input defaults to empty, meaning the environment is left untouched: the
nested `setup-rust` step still applies its own `-D warnings` default in that
case. Setting a value exports it as `RUSTFLAGS` in an "Export caller
RUSTFLAGS" step that runs *before* toolchain setup, so the nested
`setup-rust-toolchain` step defers to it and the build honours it.

## Precedence

`rust-build-release` never overwrites a `RUSTFLAGS` value the caller has
already exported: its export step checks whether the variable is set at all,
so an inherited value wins over the `rustflags` input even when that
inherited value is the empty string.

`setup-rust` has no such guard. It forwards `rustflags` to
`actions-rust-lang/setup-rust-toolchain` unconditionally, so what happens to
an inherited `RUSTFLAGS` is that action's decision, not this one's. Pass the
empty string to have it leave `RUSTFLAGS` alone.

## Usage examples

### `setup-rust`

Keep the default `-D warnings` behaviour (no input needed):

```yaml
- uses: ./.github/actions/setup-rust
  with:
    toolchain: stable
```

Pass an extra flag, replacing the default:

```yaml
- uses: ./.github/actions/setup-rust
  with:
    toolchain: nightly
    rustflags: "-D warnings -Zpolonius=next"
```

Defer to the project's `.cargo/config.toml`:

```yaml
- uses: ./.github/actions/setup-rust
  with:
    toolchain: stable
    rustflags: ""
```

### `rust-build-release`

Keep the default (environment untouched, `-D warnings` still applies via the
nested `setup-rust` step):

```yaml
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
```

Pass an extra flag required by the source tree:

```yaml
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    rustflags: "-Zpolonius=next"
```

## Which should I use?

- Use `setup-rust`'s `rustflags` input when calling that action directly.
- Use `rust-build-release`'s `rustflags` input when using the build action,
  which pins its own nested `setup-rust` step and exports the value before
  that step runs.


## Install Nixie

The `install-nixie` action installs pinned Nixie and Merman CLI releases for
Mermaid validation. The runner must already provide `cargo` and `uv` on
`PATH`.

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
  uses: leynos/shared-actions/.github/actions/install-nixie@v1
```

The action accepts three optional version inputs:

| Input            | Default | Purpose                            |
| ---------------- | ------- | ---------------------------------- |
| `nixie-version`  | `1.1.0` | Nixie CLI release                  |
| `merman-version` | `0.7.0` | Merman CLI release                 |
| `python-version` | `3.14`  | Python used by uv to install Nixie |

Override the pins when validating another supported toolchain combination:

```yaml
- name: Install Nixie
  uses: leynos/shared-actions/.github/actions/install-nixie@v1
  with:
    nixie-version: "1.2.0"
    merman-version: "0.8.0"
    python-version: "3.13"
```

When `cargo binstall` is available, the action installs Merman with a locked
binary package. If the availability probe fails, it falls back to a locked
`cargo install` build. Missing `cargo` or `uv`, and failures from either
installer, stop the action immediately. A failed installation does not export a
PATH entry.

After both installations succeed, the action appends the directory returned by
`uv tool dir --bin` to `GITHUB_PATH`. Later workflow steps can therefore invoke
`nixie` directly.
## CodeScene coverage checks

The [`upload-codescene-coverage` action][codescene-coverage-action] supports
`upload` mode for analysed branches and `check` mode for the
pull-request changed-line coverage gate. In `check` mode, check out the full
history (`fetch-depth: 0`) and provide the CodeScene `project-url`; the CLI
uses the merge base to evaluate the gate. For LCOV, the report path must end
in `.info`.

The pull request's base must already have coverage uploaded to CodeScene. If
that baseline is unavailable, `cs-coverage` cannot evaluate the gate; the
action prints the CLI's verbose diagnostic, adds the uploaded-base explanation
for status 2, and preserves the CLI's original exit status.

Checks for stacked pull requests are intentionally skipped. When the pull
request base is not the repository's default branch, the action emits a
warning and skips the remaining check-mode steps, including CLI installation,
artefact upload, and the coverage gate. This lets a stacked pull request pass
without claiming that its changed-line coverage was evaluated; rebase or merge
it onto the default branch before relying on the gate result.

[codescene-coverage-action]: ../.github/actions/upload-codescene-coverage/README.md
