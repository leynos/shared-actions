# Changelog

## v1.0.1 (2025-09-18)

- Document required workflow permissions for trusted publishing, clarify that
  the action forwards `GITHUB_TOKEN` automatically, and fix the README usage
  example to reference the local path without a version suffix.

## v1.0.0 (2025-09-18)

- Initial release: resolve release tags, ensure GitHub Release readiness, and
  publish Python distributions with uv Trusted Publishing support.
- Validate `pyproject.toml` versions against the release tag and optionally
  block dynamic version declarations.
