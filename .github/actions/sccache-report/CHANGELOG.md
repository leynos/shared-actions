# Changelog

All notable changes to the `sccache-report` action will be documented in this
file.

## Unreleased

- Add the action. It prints sccache's statistics (text and JSON), appends them
  to the job summary under the backend `setup-rust` chose, and stands down,
  with a notice and `reported=false`, when `setup-rust` reports `sccache-status`
  `fallback` or sccache is not on `PATH`. A consumer's health check conditions
  on `reported` instead of repeating the guard that keeps a fallback from
  turning red.
