# Changelog

## Unreleased

- Harden MSI version parsing and output path sanitisation.
- Improve WiX tool installation idempotency and error reporting.
- Restrict tag-derived versions to refs that match `v#.#.#` semantics and fall
  back to `0.0.0` for non-tag builds.
- Accept MSI build numbers up to `65535` and normalise architecture inputs to
  `x86`, `x64` or `arm64` before invoking WiX.
- Clarify documentation to use the `$(var.Version)` preprocessor form.
- Convert UTF-8 plain text licence files to RTF automatically when requested via the `license-plaintext-path` and `license-rtf-path` inputs.

## windows-package-v0.1.0

- Initial release of the Windows packaging composite action.
- Installs WiX v4, builds an MSI from a `.wxs` file and uploads the artefact.
