# Install Whitaker

Install the Whitaker Dylint suite with cached installer and suite state.

The action restores the installer and installed suite before installation. On a
miss, it downloads the requested prebuilt installer from Whitaker's official
GitHub release, verifies the archive against a digest pinned in this action,
and installs the executable. It never builds the installer from source. It then
runs `whitaker-installer` to install the suite.

## Trust anchor

The archive's SHA-256 digest is pinned in `installer-digests.sha256`, which
lives beside `action.yml` and is reviewed with it. Each pinned digest was
computed locally from an independently downloaded archive and cross-checked
against the release's `.sha256` sidecar. The sidecar is still downloaded and
must agree with the verified archive, but it is a consistency check rather than
the trust anchor: a compromised release could publish a matching sidecar for a
tampered archive, whereas it cannot change a digest already pinned here.

To install a version that the manifest does not pin, supply its archive digest
through `installer-sha256`. An unpinned version with no supplied digest is a
hard failure, and no archive is downloaded. The action reports the anchor it
used in the job summary as `whitaker-installer.trust-anchor=pinned` or
`whitaker-installer.trust-anchor=input`, alongside
`whitaker-installer.digest=verified` or a `mismatch`, `sidecar-mismatch`, or
`unpinned` outcome.

## Inputs

| Name                | Type   | Description                                                  | Required | Default    |
| ------------------- | ------ | ------------------------------------------------------------ | -------- | ---------- |
| `cargo-home`        | string | Cargo home that stores the cached whitaker-installer binary  | no       | `~/.cargo` |
| `installer-version` | string | Version of `whitaker-installer` to install                   | no       | `0.2.7`    |
| `installer-sha256`  | string | Archive digest for a version absent from the pinned manifest | no       | `""`       |
| `cache-provider`    | string | Built-in `github` cache or caller-owned `external` cache     | no       | `github`   |

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
