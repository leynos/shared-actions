# Changelog

## v1.0.0
- Initial version.

## v1.1.0
- Add `__auto__` default for `path` input and infer file name based on format.

## v1.2.0
- Cache CodeScene CLI using the version extracted from the installer script.

## v1.3.0
- Validate installer checksum before execution and reuse the downloaded script
  for version detection.

## v1.4.0
- Fail when `format` input is not `cobertura` or `lcov`.
