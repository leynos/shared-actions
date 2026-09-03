# Install Nixie

Install pinned Nixie and a checksum-verified Merman command-line interface
(CLI) release for Mermaid validation.

The action installs Nixie through `uv` and obtains Merman only from the
official `Latias94/merman` v0.7.0 release assets. It verifies the downloaded
archive against an embedded Secure Hash Algorithm 256-bit (SHA-256) digest
before installing the executable.
It never uses Cargo, `cargo binstall`, or a source-build fallback.

## Inputs

| Name             | Type     | Description                          | Required | Default |
| ---------------- | -------- | ------------------------------------ | -------- | ------- |
| `nixie-version`  | `string` | Nixie CLI version to install         | no       | `1.1.0` |
| `merman-version` | `string` | Verified Merman CLI release to use   | no       | `0.7.0` |
| `python-version` | `string` | Python version used to install Nixie | no       | `3.14`  |

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

- **Prerequisites**: `uv`, `curl`, and runner-provided archive and checksum
  tools must already be available. GitHub-hosted Linux and macOS runners use
  `shasum` and `tar`; Windows uses Git Bash's `cygpath` and PowerShell.
- **Merman installation**: `merman-version` currently supports only `0.7.0`.
  The action recognizes `Linux/X64`, `macOS/X64`, `macOS/ARM64`, and
  `Windows/X64`; every supported pair maps to one official release archive and
  an embedded SHA-256 digest. Any other version or platform fails before a
  download is attempted.
  The action stores Merman under
  `${XDG_CACHE_HOME:-${HOME}/.cache}/merman/0.7.0/bin` (`.exe` on Windows) and
  verifies the pinned executable digest before every reuse. Cache callers must
  include `~/.cache/merman` in their persisted paths.
- **Nixie installation**: The action reconciles the requested Nixie package
  with `uv tool install`. It uses `--force` only when the expected `nixie`
  executable shim is absent after normal reconciliation. It does not invoke
  `nixie --version`, which is not a supported probe.
- **PATH export**: After both executable checks succeed, the action appends the
  Merman and Nixie binary directories to `GITHUB_PATH` for later steps.
- **Failure behaviour**: Missing prerequisites, an unverified archive, an
  unsupported version or platform, and failed installations stop the action
  immediately without exporting a PATH entry.

## Release history

See [CHANGELOG](CHANGELOG.md).
