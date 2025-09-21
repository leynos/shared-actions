# Changelog

All notable changes to the `macos-package` action are documented in this file.

## [Unreleased]

- Initial implementation of the macOS packaging action.
- Harden payload staging to isolate intermediate artefacts and validate
  installation prefixes.
- Capture fallback build metadata separately from the package version and
  expose it as an action output.
