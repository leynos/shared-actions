# Changelog

All notable changes to the `install-whitaker` action will be documented in
this file.

## v1.0.0 (Unreleased)

- Add cached installation of the Whitaker Dylint suite
- Download and verify official prebuilt installer releases without a source
  build fallback
- Support caller-owned caches through `cache-provider: external`
- Cache the installed suite as well as the installer, keyed by `dylint.toml`
- Verify the installer archive against digests pinned in the action rather than
  the release's own `.sha256` sidecar, which a compromised release could forge
- Add the optional `installer-sha256` input for a version the pinned manifest
  does not cover, and fail closed when neither anchor is available
- Report the digest outcome and trust-anchor source in the job summary
- Default `installer-version` to `0.2.7`
