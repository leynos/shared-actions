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

## v1.4.1
- Improved error message when the coverage file is missing.
- Added restore keys to cache the CLI across minor versions.
- Removed redundant checksum validation before executing the installer.
- Reworded README reference to CHANGELOG.

## v1.4.2
- Fixed action load failure by removing unsupported `secrets` and `vars` references in `action.yml`.
- Documented required environment variables and caching usage in the README.
- Wrapped README lines to 80 columns for consistency.

## v1.4.5
- Shortened cache description line to avoid exceeding 80 columns.

## v1.5.0
- Added `access-token` and `installer-checksum` inputs, surfacing required
  variables in the UI.
- Documented the restore-keys caching pattern and expanded example usage in the
  README.

## v1.5.1
- Ensure `CS_ACCESS_TOKEN` and `CODESCENE_CLI_SHA256` environment variables are
  derived from the corresponding inputs. This prevents action load failures
  caused by unsupported `secrets` or `vars` expressions.

## v1.5.2
- Remove unsupported `env` block from the `runs` section.
- Export `CS_ACCESS_TOKEN` and `CODESCENE_CLI_SHA256` via a setup step.
