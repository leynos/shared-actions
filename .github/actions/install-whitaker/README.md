# Install Whitaker

Install the Whitaker Dylint suite with a cached `whitaker-installer`.

The action restores the installer and cargo-binstall cache before installation.
When the installer is not cached, it prefers `cargo binstall` and falls back to
`cargo install` when cargo-binstall is unavailable. It then runs
`whitaker-installer` to install the suite.

## Inputs

| Name                | Description                                                     | Required | Default    |
| ------------------- | --------------------------------------------------------------- | -------- | ---------- |
| `cargo-home`        | Cargo home that stores the cached whitaker-installer binary     | no       | `~/.cargo` |
| `installer-version` | Version of `whitaker-installer` to install                      | no       | `0.2.6`    |

## Outputs

| Name | Description                     |
| ---- | ------------------------------- |
| None | This action exposes no outputs. |

## Usage

```yaml
- name: Check out the repository
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

- name: Set up Rust
  uses: leynos/shared-actions/.github/actions/setup-rust@aebb3f5b831102e2a10ef909c83d7d50ea86c332 # setup-rust-v1

- name: Install Whitaker
  uses: ./.github/actions/install-whitaker

- name: Lint
  run: make lint
```

The repository must be checked out before invoking this local action; use the
relative path without a version suffix. The runner must have Cargo available.
If `cargo binstall --version` succeeds, the action installs the requested
version with `cargo binstall --locked`.
Otherwise, it builds the same version from crates.io with
`cargo install --locked`.

The `cargo-home` input defaults to `~/.cargo`; it controls both the cached
installer location and the installation step's `CARGO_HOME`. That step prepends
`${CARGO_HOME}/bin` to its `PATH`.

## Release history

See the [changelog](CHANGELOG.md).
