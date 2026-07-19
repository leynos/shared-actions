# Install Whitaker

Install the Whitaker Dylint suite with a cached `whitaker-installer`.

The action restores the installer and cargo-binstall cache before installation.
When the installer is not cached, it prefers `cargo binstall` and falls back to
`cargo install` when cargo-binstall is unavailable. It then runs
`whitaker-installer` to install the suite.

## Inputs

| Name                | Description                               | Required | Default |
| ------------------- | ----------------------------------------- | -------- | ------- |
| `installer-version` | Version of `whitaker-installer` to install | no       | `0.2.6` |

## Outputs

This action has no outputs.

## Usage

```yaml
- name: Set up Rust
  uses: leynos/shared-actions/.github/actions/setup-rust@v1

- name: Install Whitaker
  uses: leynos/shared-actions/.github/actions/install-whitaker@v1

- name: Lint
  run: make lint
```

The runner must have Cargo available. If `cargo binstall --version` succeeds,
the action installs the requested version with `cargo binstall --locked`.
Otherwise, it builds the same version from crates.io with
`cargo install --locked`.

## Release history

See the [changelog](CHANGELOG.md).
