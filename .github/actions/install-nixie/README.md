# Install Nixie

Install pinned Nixie and Merman CLI releases for Mermaid validation.

The action installs Nixie through `uv` and its Merman rendering backend through
Cargo. It uses `cargo binstall` when available and falls back to a locked
`cargo install` build from crates.io.

## Inputs

| Name             | Description                              | Required | Default |
| ---------------- | ---------------------------------------- | -------- | ------- |
| `nixie-version`  | Nixie CLI version to install             | no       | `1.1.0` |
| `merman-version` | Merman CLI version to install            | no       | `0.7.0` |
| `python-version` | Python version used to install Nixie     | no       | `3.14`  |

## Outputs

| Name   | Description                                             |
| ------ | ------------------------------------------------------- |
| _None_ | The action emits no outputs.                            |

## Usage

```yaml
- name: Set up Rust
  uses: leynos/shared-actions/.github/actions/setup-rust@v1

- name: Install Nixie
  uses: leynos/shared-actions/.github/actions/install-nixie@v1

- name: Validate Mermaid diagrams
  run: nixie --renderer merman
```

To override the pinned versions:

```yaml
- uses: leynos/shared-actions/.github/actions/install-nixie@v1
  with:
    nixie-version: "1.1.0"
    merman-version: "0.7.0"
    python-version: "3.14"
```

## Behaviour

- **Prerequisites**: `cargo` and `uv` must already be available on `PATH`.
  The repository's `setup-rust` action provisions both tools and
  `cargo-binstall`.
- **Merman installation**: When `cargo binstall` is available, the action
  installs the selected Merman release from a binary package with locked
  metadata. Otherwise it builds the exact selected release from crates.io with
  `cargo install --locked`.
- **Nixie installation**: The action uses `uv tool install` with the selected
  Python and exact Nixie release.
- **Failure behaviour**: Missing prerequisites and failed installations stop
  the action immediately with a non-zero exit status.

## Release history

See [CHANGELOG](CHANGELOG.md).
