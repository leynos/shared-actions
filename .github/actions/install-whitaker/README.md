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

The pinned manifest takes precedence over the `installer-sha256` input. When
the manifest pins the asset, the pinned digest is the anchor; a supplied digest
that disagrees with it is rejected before anything is downloaded, and the error
names both digests. Supply `installer-sha256` only for an asset the manifest
does not pin. An asset with neither anchor is a hard failure, again before any
download.

The action reports the anchor it used in the job summary as
`whitaker-installer.trust-anchor=pinned` or
`whitaker-installer.trust-anchor=input`, alongside
`whitaker-installer.digest=verified` or a `mismatch`, `sidecar-mismatch`,
`conflict`, or `unpinned` outcome.

## Lifecycle

The action separates the release lifecycle into explicit steps.

`Resolve Whitaker release` is a thin adapter over
`scripts/resolve-release.sh`, which holds the resolution itself. That script is
a pure query: it selects the platform asset, looks up the pinned digest,
applies the precedence rule, and decides whether the cache already holds an
executable installer of the requested version, then prints what it computed.
It writes no file, emits no metric, prints no annotation, and reports an
expected resolution failure as a printed record rather than by exiting
non-zero. The step captures that record and writes it to a step output, which
is the only way to carry a value across a composite step boundary.

`Publish Whitaker resolution` owns every externally visible effect of that
resolution. It writes the step outputs, emits the metrics, prints the notices,
and fails the job when resolution recorded an error.

`Download Whitaker release`, `Verify Whitaker release`, `Extract Whitaker
installer`, and `Install Whitaker installer` each perform one of those actions
and nothing else. The staging directory lives under `RUNNER_TEMP` and is
removed once the installer is in place.

## Transfer telemetry

The archive transfer and the `.sha256` sidecar transfer each report one
bounded record, through a `::notice` and a job-summary metric naming the
outcome, the HTTP status, the byte count, the elapsed seconds, and the number
of attempts:

```text
whitaker-installer.transfer.archive=ok http=200 bytes=2469093 seconds=1.204 attempts=1
```

The attempt count comes from curl's `num_retries`, which was added in curl
8.9.0. On an older curl the action reports `attempts=unknown` and still records
the other fields.

## Cached installer freshness

The action writes `.whitaker-installer-version` beside the installer, recording
which `installer-version` it installed, and caches that marker with the
installer. A cached installer is reused only when the marker names the
requested version. A marker naming another version, or no marker at all,
reports `whitaker-installer.cache-entry=stale` and falls through to the
verified download. This matters for `cache-provider: external`, where a
persistent Cargo home would otherwise keep serving an installer built for an
older version.

## Inputs

| Name                | Type   | Description                                                  | Required | Default    |
| ------------------- | ------ | ------------------------------------------------------------ | -------- | ---------- |
| `cargo-home`        | string | Cargo home that stores the cached whitaker-installer binary  | no       | `~/.cargo` |
| `installer-version` | string | Version of `whitaker-installer` to install                   | no       | `0.2.8`    |
| `installer-sha256`  | string | Archive digest for an asset absent from the pinned manifest  | no       | `""`       |
| `suite-version`     | string | Git reference the lint suite is built from                   | no       | `""`       |
| `cache-provider`    | string | Built-in `github` cache or caller-owned `external` cache     | no       | `github`   |

## What is pinned, and what is not

The installer is pinned thoroughly: a release archive verified against
[`installer-digests.sha256`](installer-digests.sha256), never built from
source.

**The lint suite is a separate decision.** By default the installer builds it
from the Whitaker default branch tip, so a change on that branch alters lint
results with no commit in the consuming repository. That is how
`pg-embed-setup-unpriv`'s lint gate stayed red from 2026-08-19 to 2026-09-04:
the same installer version and the same toolchain, a different suite commit,
and nothing in the consumer to point at.

`suite-version` names a tag, branch or commit to build the suite from instead,
so a suite change arrives as a reviewed bump:

```yaml
- uses: ./.github/actions/install-whitaker
  with:
    suite-version: v0.2.8
```

It costs a source build. Prebuilt lint libraries are published only for the
branch tip, so a pin cannot be served from them, and the installer does not
attempt the download when one is set. The trade is install time for
reproducibility, and it stays that trade even where artefacts exist: a lint
library must be built with the exact toolchain that loads it.

A pin needs installer 0.2.8 or later, and cannot be applied when the workflow
runs inside a Whitaker checkout, because checking out a reference there would
move the working tree.

Each run records which arm it took as
`metric whitaker-installer.suite=<pinned-commit|pinned-mutable-ref|default-branch-tip>`,
so a lane that never chose can see the exposure rather than discover it from a
red gate. The pinned arm is split because only a full commit identifier is
immutable: a branch or a tag can advance without the calling repository
changing a line, which is the same drift an unpinned lane suffers.

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
SHA-256 utility, and `tar`. Both archive formats are extracted with `tar`:
bsdtar, the bundled `tar` on Windows and macOS runners, reads zip as well as
gzip, and `unzip` is not present on every runner image. Missing
official release assets are hard failures; there is no Cargo or source-build
fallback.

The `cargo-home` input defaults to `~/.cargo`; it controls the cached
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
