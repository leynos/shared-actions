# Generate Coverage Design Notes

## Artifact Naming Enhancements (2025-11-06)

- **Motivation**: Consuming jobs need unique coverage artifacts per platform so
  downloads can target the correct OS/architecture combination without relying
  on matrix indexes alone.
- **Decision**: The action now derives a canonical artifact name via
  `set_outputs.py`, slugging the runner OS/architecture (from `RUNNER_*` envs or
  `uname`) and appending them to the existing `format-job-index` stem.
- **Customisation**: A new `artifact-extra-suffix` input allows callers to
  append an additional, sanitised tag (for channels or feature flags) after the
  OS/arch suffix so downstream automation can disambiguate nightly/beta runs.
- **Implementation**: The helper script switched to `cyclopts` for env-driven
  CLI parsing and uses `plumbum` (via `cmd_utils_loader.run_cmd`) to collect
  fallback platform details, keeping execution consistent with other action
  scripts.
