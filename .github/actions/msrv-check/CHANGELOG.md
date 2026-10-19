# Changelog

## Unreleased

- Initial version. Reads `rust-version` from the manifest (following workspace
  inheritance), installs that toolchain, drops the runner checkout's cargo
  config unless asked to keep it, and runs one `cargo check`.
