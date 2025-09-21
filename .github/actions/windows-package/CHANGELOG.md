# Changelog

## Unreleased

- Harden MSI version parsing and output path sanitisation.
- Improve WiX tool installation idempotency and error reporting.

## windows-package-v0.1.0

- Initial release of the Windows packaging composite action.
- Installs WiX v4, builds an MSI from a `.wxs` file and uploads the artefact.
