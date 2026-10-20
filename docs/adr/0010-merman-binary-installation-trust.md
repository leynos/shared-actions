# ADR 0010: Install Merman from upstream prebuilt binaries

**Status:** Accepted **Date:** 2026-10-20

## Context

The Install Nixie action previously selected Merman release archives itself,
maintained platform-specific checksum data, extracted the executable, and
reconciled its cache against those digests. That duplicated release selection
and archive-handling logic in the composite action. The action now delegates
Merman package and prebuilt artefact selection to `cargo-binstall`.

This changes the verification boundary. The action no longer carries a
release-specific SHA-256 digest for each Merman binary. It can verify that an
installed executable runs and reports the exact requested version, but that is
not cryptographic verification of the executable's contents.

## Decision

- Probe `cargo binstall -V` and run the `cargo-bins/cargo-binstall` action at a
  full commit SHA only when the subcommand is unavailable. Pin the bootstrapped
  cargo-binstall version.
- Install `merman-cli` at the exact validated numeric version and selected
  runner target. Disable source-build fallback; a missing compatible binary
  release fails the action.
- Store each version in a deterministic cache directory. Reuse an entry only
  when the executable runs and reports the requested version; otherwise remove
  the stale entry and reinstall it.
- Treat the crate metadata and checksums, cargo-binstall's release selection,
  and any signature or checksum checks it performs as upstream trust inputs.
  Unsigned binary releases remain acceptable. The action verifies execution and
  the reported version, not a locally pinned digest.
- Capture the job token under a dedicated environment variable, clear inherited
  credential aliases before running Merman or `uv`, and scope the captured
  token to the `cargo binstall` command only.
- Keep Nixie installation and version reconciliation with `uv`.

## Consequences

The action has no custom Merman downloader, archive extractor, or per-release
digest table, and new numeric Merman versions do not require updating such a
table. A platform without a compatible prebuilt release fails instead of
compiling from source. Cache version checks detect stale or mismatched entries
but do not establish binary authenticity. Workflows that require independently
pinned binary digests must use a different installation boundary.

## References

- [Install Nixie action documentation](../../.github/actions/install-nixie/README.md)
- [Install Nixie maintenance guidance](../developers-guide.md#install-nixie-action-maintenance)
- [PR #412](https://github.com/leynos/shared-actions/pull/412)
