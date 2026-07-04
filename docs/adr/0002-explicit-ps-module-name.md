# ADR 0002: Explicit ps-module-name for PowerShell sidecars

**Status:** Accepted **Date:** 2026-05-19

## Context

The `stage-release-artefacts` action must stage Windows PowerShell MAML
sidecars without guessing module names. PowerShell help sidecars are copied as
ordinary configured artefacts, but downstream workflow steps also need a stable
`powershell_help_dir` output that points at the staged module directory.

Multiple module directories may exist in a staging directory. Auto-detection is
therefore ambiguous, and on case-insensitive filesystems it can also produce
false positives when unrelated staged files differ only by case from the
expected module directory.

## Decision

Require callers to provide an explicit `ps-module-name` input when they want a
PowerShell module directory exported.

Only export `powershell_help_dir` when the named module is a direct child
directory under the staging directory and at least one staged file resides
beneath it. Disallow empty names, `"."`, `".."`, names containing path
separators, traversal attempts, and nested module paths.

## Consequences

Non-Windows callers omit `ps-module-name` with zero cost. Windows callers that
stage PowerShell sidecars must provide the module name explicitly.

The output is safer and deterministic. The action avoids ambiguous
auto-detection, parent-directory traversal, nested module paths, and fewer
false positives on case-insensitive filesystems.

## Implementation Notes

`stage_common.pipeline._resolve_powershell_help_dir` resolves the public
`powershell_help_dir` output after artefact staging has completed. Its
validation helpers reject invalid module names before checking the staged
module directory.

The workflow output layer normalizes paths with `Path.as_posix()` so exported
paths use forward slashes consistently across platforms.

## References

- PR `#265`
- Tests: `test_pipeline.py` Windows module staging cases and
  `test_stage_cli.py` snapshots.
