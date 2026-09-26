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

`Resolve Whitaker release` is a thin adapter over `scripts/resolve-release.sh`,
which holds the resolution itself. That script is a pure query: it selects the
platform asset, looks up the pinned digest, applies the precedence rule, and
decides whether the cache already holds an executable installer of the
requested version, then prints what it computed. It writes no file, emits no
metric, prints no annotation, and reports an expected resolution failure as a
printed record rather than by exiting non-zero. The step captures that record
and writes it to a step output, which is the only way to carry a value across a
composite step boundary.

`Publish Whitaker resolution` owns every externally visible effect of that
resolution. It writes the step outputs, emits the metrics, prints the notices,
and fails the job when resolution recorded an error.

`Download Whitaker release`, `Verify Whitaker release`,
`Extract Whitaker installer`, and `Install Whitaker installer` each perform one
of those actions and nothing else. The staging directory lives under
`RUNNER_TEMP` and is removed once the installer is in place.

## Transfer telemetry

The archive transfer and the `.sha256` sidecar transfer each report one bounded
record, through a `::notice` and a job-summary metric naming the outcome, the
HTTP status, the byte count, the elapsed seconds, and the number of attempts:

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
requested version. A marker naming another version, or no marker at all, reports
`whitaker-installer.cache-entry=stale` and falls through to the verified
download. This matters for `cache-provider: external`, where a persistent Cargo
home would otherwise keep serving an installer built for an older version.

## Inputs

| Name                | Type   | Description                                              | Required | Default        |
| ------------------- | ------ | -------------------------------------------------------- | -------- | -------------- |
| `cargo-home`        | string | Cargo home holding the cached installer binary           | no       | `~/.cargo`     |
| `installer-version` | string | Version of `whitaker-installer`, 0.2.9 or later          | no       | `0.2.9`        |
| `installer-sha256`  | string | Digest for an asset the manifest does not pin            | no       | `""`           |
| `suite-version`     | string | Refused: any non-empty value fails the step              | no       | `""`           |
| `cache-provider`    | string | Built-in `github` or caller-owned `external`             | no       | `github`       |
| `ci-mode`           | string | Check the published assets before the installer          | no       | `true`         |
| `cranelift`         | string | Also add rustc-codegen-cranelift for the suite toolchain | no       | `false`        |
| `github-token`      | string | Read the rolling release without the anon limit          | no       | `github.token` |

## What is pinned, and what is not

The installer is pinned: an exact version, taken from a release archive
verified against [`installer-digests.sha256`](installer-digests.sha256), never
built from source. The default is the version this action pins, and a caller
may name a newer one, but never one older than 0.2.9, the first release with
`--no-source-fallback`.

The lint suite is not pinned, and cannot be. Whitaker's lints are a rolling
release: every merge to its default branch republishes prebuilt lint libraries,
and every consumer takes the suite from there. `suite-version` stays declared
only so that setting it fails the step. An undeclared input is dropped by
GitHub with a warning, so a pin would look honoured while doing nothing.
`allow-suite-pin` has been removed.

## Nothing is built from source

Every run passes `--no-source-fallback` to the installer, whatever `ci-mode`
says. When a published lint library or Dylint tool archive is missing, the
installer fails before Cargo starts instead of compiling it. A source build
succeeds, which is the problem: the run looks healthy while it has tested
something else, more slowly.

The installer extracts cargo-dylint and dylint-link into `~/.local/bin` (or
`XDG_BIN_HOME`) and proves cargo-dylint by running `cargo dylint`, so the
action puts that directory on `PATH` for the installer and, through
`GITHUB_PATH`, for the steps after it. The Windows images do not have it on
`PATH` by default.

The installer's output, stdout and stderr, is still read afterwards, as a
backstop. A run whose output reports a source build fails, and records
`whitaker-installer.suite-source=<prebuilt|source>`.

`ci-mode`, on by default, adds a check before the installer runs. It confirms
that the rolling release carries this target's manifest, the lint archive that
manifest names, and both Dylint tool archives. It retries five times over about
thirty seconds, because a republish takes six or seven, and fails with the URL
if an asset is still absent. Turn it off only where the rolling release cannot
be reached, such as a local reproduction; the installer still refuses a source
build.

`cranelift: true` passes `--cranelift`, so the installer adds the
`rustc-codegen-cranelift` component to the lint suite's toolchain through
rustup. A repository whose builds select the Cranelift backend needs it for
`whitaker` to compile the checkout; nothing is built from source either way.

The resolved nightly is recorded as
`whitaker-installer.suite-toolchain=<toolchain>`, so a lint result can be tied
to the compiler that produced the libraries. Each run also records
`whitaker-installer.suite=default-branch-tip`.

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
gzip, and `unzip` is not present on every runner image. Missing official
release assets are hard failures; there is no Cargo or source-build fallback.

The `cargo-home` input defaults to `~/.cargo`; it controls the cached installer
location. In `github` mode, the same cache also owns `~/.local/share/whitaker`,
keyed by `dylint.toml`.

Set `cache-provider: external` when the caller mounts these paths through a
Namespace cache volume; the action then skips its GitHub cache and reports the
built-in cache as disabled. Mount `~/.local/share`, not the terminal
`~/.local/share/whitaker` directory: the installer distinguishes an absent
checkout from an existing Git checkout, while a volume mount makes its target
exist even when empty.

## Release history

See the [changelog](CHANGELOG.md).
