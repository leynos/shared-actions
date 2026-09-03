# Migrating to verified prebuilt CI tools

This guide covers the next major tags of `install-whitaker` and
`generate-coverage`. Both actions now install their CI tooling —
`whitaker-installer` and `cargo-nextest` — from verified prebuilt release
archives instead of building or fetching them through Cargo. Read this before
pulling in the next major tag of either action.

## What changed, and why

Both actions now implement the same trusted-prebuilt-binary policy: install a
named version from its official GitHub release, verify the downloaded archive
against a digest pinned inside this repository, and refuse to proceed when that
digest is absent or wrong. Neither action builds its tool from source, and
neither falls back to an unverified installation path.

The pinned digest, not a release's own checksum sidecar, is the trust anchor
for both actions. A compromised release could publish a matching `.sha256`
sidecar for a tampered archive, so trusting a sidecar alone would not catch
that tampering. Pinning the digest in this repository means an attacker who
compromises the upstream release cannot also rewrite the digest already
committed here.

The two actions differ in how they treat the sidecar. `install-whitaker` still
downloads the release's `.sha256` sidecar and compares it with the archive
digest it just verified, but only as a consistency check; the pinned manifest
remains the anchor regardless of the outcome. `generate-coverage`'s
`install_cargo_nextest.py` does not fetch or compare a sidecar at all — it
verifies the downloaded archive directly against the digest pinned in the script
(`ReleaseAsset.sha256`).

## Behaviour that is no longer available

Neither action falls back to `cargo install`, `cargo binstall`, or a source
build:

- `install-whitaker` previously installed `whitaker-installer` with
  `cargo binstall`, falling back to `cargo install` (a source build) when
  `cargo-binstall` was unavailable. Both paths are removed; a caller who relied
  on either must use a version this repository's pinned digest manifest covers,
  or supply a verified `installer-sha256` for the resolved asset.
- `generate-coverage` previously installed `cargo-nextest` through
  cargo-binstall, which could itself fall back to a QuickInstall substitute or
  a source build. That substitution path is gone. A missing or unverifiable
  prebuilt `cargo-nextest` binary is now a hard failure with no further
  fallback.

A caller who relied on cargo-binstall reaching an unpinned or
QuickInstall-provided `cargo-nextest` build must either use a version that this
repository's pinned digests cover, or supply its own verified binary on `PATH`
before `generate-coverage` runs (see `install_cargo_nextest.py`'s
`_resolve_nextest_binary` check, which accepts a preinstalled binary that
already matches the pinned digest).

## `install-whitaker` changes

### Default version

The default `installer-version` input moved from `0.2.6` to `0.2.7`. Both
versions remain pinned in `installer-digests.sha256` for all five supported
targets, so a caller that explicitly pins `installer-version: "0.2.6"` is
unaffected.

### Pinned digest manifest

`.github/actions/install-whitaker/installer-digests.sha256` is a plain
`sha256sum` manifest that sits beside `action.yml` and is reviewed with it.
Each line pairs a digest with an asset filename, for example:

```text
78959394c6bbf77eb80ce7f6818d1dedabea68224a3603b3481ee927f8be9fa0  whitaker-installer-aarch64-apple-darwin-v0.2.7.tgz
```

### The `installer-sha256` input

The action gained an optional `installer-sha256` input: a caller-supplied
SHA-256 digest of the release archive for the current runner. Its description in
`action.yml` states the precedence rule:

> SHA-256 digest of the whitaker-installer release archive for this runner.
> The action's pinned digest manifest takes precedence; supply this only for
> an asset the manifest does not pin. A value that disagrees with a pinned
> digest is rejected.

### Precedence rule

The `Resolve Whitaker release` step applies this order:

1. If the manifest pins a digest for the resolved asset, that digest is the
   anchor, reported as `whitaker-installer.trust-anchor=pinned`. A supplied
   `installer-sha256` that matches the pinned digest is accepted; one that
   disagrees is rejected before any download.
2. If the manifest does not pin the asset, a supplied `installer-sha256`
   becomes the anchor, reported as `whitaker-installer.trust-anchor=input`.
3. If neither the manifest nor the input supplies a digest, the step fails
   before anything is downloaded.

### Failure modes

The action's caller-visible digest failures, each reported through both a
`::error` annotation and a job-summary metric, are:

- `whitaker-installer.digest=mismatch` — the downloaded archive's computed
  SHA-256 does not match the expected digest. The error names both digests, for
  example
  `archive digest mismatch for <asset>: expected <expected> but
  computed <actual>`.
- `whitaker-installer.digest=sidecar-mismatch` — the release's own
  `.sha256` sidecar disagrees with the archive digest that was just verified
  against the pinned or supplied anchor. The error names both digests:
  `release sidecar digest <sidecar> disagrees with the verified
  archive digest <actual> for <asset>`.
- `whitaker-installer.digest=conflict` — the manifest pins a digest for the
  asset and the supplied `installer-sha256` disagrees with it. The error names
  both digests:
  `installer-sha256 <supplied> conflicts with the
  digest pinned for <asset> (<pinned>); remove the input or correct it`.
- `whitaker-installer.digest=unpinned` — the asset has neither a pinned nor
  a supplied digest. The error reads
  `no pinned SHA-256 for <asset>; supply
  the installer-sha256 input to install version <version>`.

Each of these stops the workflow step; none of them falls back to a source
build.

## Installing a version the manifest does not pin

To install a `whitaker-installer` version the manifest does not cover, supply
its archive digest through `installer-sha256`. Compute that digest yourself
from an independently downloaded copy of the archive with `sha256sum`; do not
copy it from the release's `.sha256` sidecar, since the sidecar is not a trust
anchor.

## Adding a version to the manifest

To get a version added to `installer-digests.sha256` so future callers do not
need to supply `installer-sha256` themselves:

1. Download each of the five target archives for the new version directly
   from the Whitaker GitHub release.
2. Compute each archive's digest locally with `sha256sum`.
3. Cross-check each computed digest against the release's own `.sha256`
   sidecar for that asset. A disagreement between the two blocks the change;
   investigate before pinning anything.
4. Append one `sha256sum`-formatted line per asset to
   `installer-digests.sha256`, alongside the existing entries.
5. Open a pull request that reviews the new manifest lines together with any
   `action.yml` change, per this repository's normal review process.

## `generate-coverage` change

`generate-coverage` installs `cargo-nextest` from its pinned official
`nextest-rs/nextest` release archive through
`.github/actions/generate-coverage/scripts/install_cargo_nextest.py`. The
script verifies both the downloaded archive and the extracted executable
against digests pinned in the script, and it never invokes Cargo. There is no
source-build fallback: a missing or unverifiable prebuilt binary is a hard
failure.

The `use-cargo-nextest` input is unchanged in shape — it still defaults to
`"true"` and still selects whether Rust coverage runs through
`cargo llvm-cov nextest` or `cargo llvm-cov` directly — but a caller that sets
it to `"true"` now gets the verified-prebuilt installation path described above
instead of the previous cargo-binstall path. A caller that sets
`use-cargo-nextest: "false"` is unaffected, since the action never installs
`cargo-nextest` in that mode.

## Checklist

- [ ] Confirm which `install-whitaker` and `generate-coverage` major tags you
      consume, and read their changelogs for the exact release that
      introduces verified prebuilt installation.
- [ ] If you pin `installer-version` explicitly, confirm it is one of the
      versions listed in `installer-digests.sha256`, or supply a verified
      `installer-sha256`.
- [ ] If you relied on a cargo-binstall QuickInstall substitute or a source
      build for `cargo-nextest`, replace that reliance with a version this
      repository pins, or preinstall a verified binary on `PATH` before
      `generate-coverage` runs.
- [ ] Remove any workaround that assumed a `cargo install` or
      `cargo binstall` fallback existed for either tool; none remains.
- [ ] Re-run your workflow against the new major tag and confirm the job
      summary reports `whitaker-installer.digest=verified` (or the
      equivalent `cargo-nextest` verification) rather than a failure metric.
