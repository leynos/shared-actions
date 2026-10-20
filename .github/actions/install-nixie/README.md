# Install Nixie

Install pinned Nixie and a version-checked Merman command-line interface (CLI)
binary for Mermaid validation.

The action installs Nixie through `uv` and installs Merman with
`cargo-binstall`. It uses prebuilt binaries only; it does not fall back to
compiling Merman from source.

## Inputs

| Name             | Type     | Description                           | Required | Default |
| ---------------- | -------- | ------------------------------------- | -------- | ------- |
| `nixie-version`  | `string` | Nixie CLI version to install          | no       | `1.1.0` |
| `merman-version` | `string` | Numeric Merman CLI version to install | no       | `0.7.0` |
| `python-version` | `string` | Python version used to install Nixie  | no       | `3.14`  |

## Outputs

| Name   | Description                  |
| ------ | ---------------------------- |
| _None_ | The action emits no outputs. |

## Usage

```yaml
- name: Set up Rust
  uses: leynos/shared-actions/.github/actions/setup-rust@v1

- name: Install Nixie
  uses: leynos/shared-actions/.github/actions/install-nixie@v1

- name: Validate Mermaid diagrams
  run: nixie --renderer merman
```

To use the action from this repository:

```yaml
- name: Check out the repository
  uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

- name: Install Nixie
  uses: ./.github/actions/install-nixie
```

For SHA-pinned references, use the action commit SHA:

```yaml
uses: leynos/shared-actions/.github/actions/install-nixie@<SHA>
```

To override the Nixie and Python pins:

```yaml
- uses: leynos/shared-actions/.github/actions/install-nixie@v1
  with:
    nixie-version: "1.1.0"
    python-version: "3.14"
```

## Behaviour

- **Prerequisites**: `uv` and Cargo must be available. The action probes
  `cargo binstall -V`; if that command fails, it installs cargo-binstall
  `1.22.0` through the upstream action pinned to a full commit SHA. The later
  installation step checks for `uv` and Cargo; if either is absent, the
  bootstrap may already have run, but Merman and Nixie are not installed.
- **Version inputs**: `nixie-version` must contain three numeric components
  (`major.minor.patch`), and `python-version` must contain two or three numeric
  components (`major.minor` or `major.minor.patch`). Neither accepts leading
  zeroes except for a component equal to zero.
- **Merman installation**: `merman-version` must have the numeric
  `major.minor.patch` form, with no leading zeroes except for a zero component.
  The action recognizes `Linux/X64`, `macOS/X64`, `macOS/ARM64`, and
  `Windows/X64`, and passes the corresponding target triple to cargo-binstall.
  Merman `0.7.0` publishes binary assets for all four targets. Other accepted
  versions must also publish a compatible prebuilt binary; otherwise
  installation fails with a clear error. The action passes an exact version
  requirement, disables cargo-binstall's `compile` strategy, and installs to
  the version-specific directory
  `${XDG_CACHE_HOME:-${HOME}/.cache}/merman/<version>/bin` (`.exe` on Windows).
  Cache reuse requires an executable whose `--version` output contains the
  exact requested version token. A mismatched or unusable cache entry is
  removed and reconciled through cargo-binstall. Cache callers should persist
  `~/.cache/merman`.
- **Release lookup authentication**: cargo-binstall resolves the release
  through the GitHub API, so the action passes `${{ github.token }}` to that
  one command. That lifts cold installs from the address-shared anonymous rate
  limit to the repository's authenticated budget. The token is mapped under
  `MERMAN_GITHUB_TOKEN`, captured, and unset before any other command runs, so
  the cached or freshly installed Merman binary, and Nixie, never inherit it. A
  job with no token falls back to unauthenticated requests.
- **Merman trust and verification**: the action trusts cargo-binstall `1.22.0`,
  crates.io package metadata and checksums, and the binary sources selected by
  cargo-binstall (the crate's release metadata and/or its quick-install binary
  source). Downloads use cargo-binstall's HTTPS handling and signatures are
  checked when supplied, but unsigned binary artefacts are allowed. The action
  no longer embeds or checks release-specific SHA-256 digests for Merman binary
  archives. Its local verification confirms that the installed command runs and
  reports the requested version; that version check does not authenticate the
  binary's publisher or contents.
- **Nixie installation**: The action reconciles the requested Nixie package
  with `uv tool install`. It uses `--force` only when the expected `nixie`
  executable shim is absent after normal reconciliation. It does not invoke
  `nixie --version`, which is not a supported probe. Before invoking uv, it
  prunes interpreter-bound script environments restored by the caller while
  keeping reusable package downloads. If the shim is still missing or is not
  executable after repair, installation fails.
- **PATH export**: After both executable checks succeed, the action appends the
  Merman and Nixie binary directories to `GITHUB_PATH` for later steps, using a
  Windows-format path for Merman on Windows runners.
- **Failure behaviour**: Missing prerequisites, an unsupported target, an
  invalid version, a missing prebuilt release, or a failed or
  version-mismatched installation stop the action immediately. A missing or
  non-executable Nixie shim after repair also fails. Installation failures do
  not append the Merman or Nixie directories to `GITHUB_PATH`.

## Release history

See [CHANGELOG](CHANGELOG.md).
