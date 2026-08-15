# Install Whitaker

Install the Whitaker Dylint suite with a cached `whitaker-installer`.

The action restores the installer and cargo-binstall cache before installation.
When the installer is not cached, it prefers `cargo binstall` and falls back to
`cargo install` when cargo-binstall is unavailable. It then runs
`whitaker-installer` to install the suite.

## Inputs

| Name                | Description                                | Required | Default |
| ------------------- | ------------------------------------------ | -------- | ------- |
| `installer-version` | Version of `whitaker-installer` to install | no       | `0.2.6` |

## Outputs

| Name | Description                     |
| ---- | ------------------------------- |
| None | This action exposes no outputs. |

## Usage

```yaml
- name: Set up Rust
  uses: leynos/shared-actions/.github/actions/setup-rust@aebb3f5b831102e2a10ef909c83d7d50ea86c332 # setup-rust-v1

- name: Install Whitaker
  uses: ./.github/actions/install-whitaker@v1

- name: Lint
  run: make lint
```

The runner must have Cargo available. If `cargo binstall --version` succeeds,
the action installs the requested version with `cargo binstall --locked`.
Otherwise, it builds the same version from crates.io with
`cargo install --locked`.

## Release history

See the [changelog](CHANGELOG.md).
