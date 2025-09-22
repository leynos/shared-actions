# Changelog

## Unreleased

- Centralise Linux target triple handling in a shared helper that powers both
  the packaging CLI and the rust-build-release staging step.
- Restore RPM dependency fallback behaviour so blank inputs reuse the Debian
  list and update documentation for canonical nfpm format names.
- Switch the packaging helper to Cyclopts-driven environment parsing and remove
  inline shell argument assembly.
- Initial release of the `linux-packages` composite action.
