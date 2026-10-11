# Changelog

## v1.0.0 (Unreleased)

- Add pinned installation of yamllint 1.38.0, actionlint 1.7.12 and
  shellcheck 0.11.0.
- Install actionlint and shellcheck only from official release archives
  verified against embedded SHA-256 digests; unsupported platforms and digest
  mismatches fail closed.
- Reuse a version-scoped cache directory when both binaries already report the
  pinned versions.
