# Install Whitaker

Install the Whitaker Dylint suite with cached installer and suite state.

The action restores the installer and installed suite before installation. On a
miss, it
downloads the requested prebuilt installer and checksum from Whitaker's
official GitHub release, verifies the archive, and installs the executable. It
never builds the installer from source. It then runs `whitaker-installer` to
install the suite.

## Inputs

| Name                | Type   | Description                                                 | Required | Default    |
| ------------------- | ------ | ----------------------------------------------------------- | -------- | ---------- |
| `cargo-home`        | string | Cargo home that stores the cached whitaker-installer binary | no       | `~/.cargo` |
| `installer-version` | string | Version of `whitaker-installer` to install                  | no       | `0.2.6`    |
| `cache-provider`    | string | Built-in `github` cache or caller-owned `external` cache    | no       | `github`   |

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
relative path without a version suffix. The runner must provide Bash, curl, an
SHA-256 utility, and the platform archive utility (`tar` or `unzip`). Missing
official release assets are hard failures; there is no Cargo or source-build
fallback.

The `cargo-home` input defaults to `~/.cargo`; it controls both the cached
installer location. In `github` mode, the same cache also owns
`~/.local/share/whitaker`, keyed by `dylint.toml`.

Set `cache-provider: external` when the caller mounts these paths through a
Namespace cache volume; the action then skips its GitHub cache and reports the
built-in cache as disabled. Mount `~/.local/share`, not the terminal
`~/.local/share/whitaker` directory: the installer distinguishes an absent
checkout from an existing Git checkout, while a volume mount makes its target
exist even when empty.

## Release history

See the [changelog](CHANGELOG.md).
