# Changelog

## v1.0.0 (2025-12-26)

- Initial release migrated from leynos/netsuke
- Extract package metadata (name, version, description, bin-name) from Cargo.toml
  (Tom's Obvious, Minimal Language)
- Support workspace version inheritance
- Export fields as both GitHub outputs and environment variables
- Binary name detection with [[bin]] fallback to [package].name
