# Changelog

## v1.0.0 (Unreleased)

- Add pinned Nixie and Merman CLI installation.
- Install Merman 0.7.0 only from official release archives verified with pinned
  SHA-256 checksums; unsupported versions and platforms now fail closed.
- Support the verified Windows/X64 Merman release and executable shims.
- Remove the Cargo, `cargo binstall`, and source-build installation paths.
- Keep Nixie and Python overrides, reconciling normally on a warm cache and
  forcing a reinstall only to repair a missing Nixie executable shim.
- Expose the verified Merman and Nixie executable directories to later steps.
