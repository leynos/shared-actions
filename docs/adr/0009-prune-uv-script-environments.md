# ADR 0009: Do not reuse interpreter-bound uv script environments

**Status:** Accepted **Date:** 2026-10-19

## Context

The shared uv cache contains downloaded packages and `environments-v2`
environments created by script runs such as `uv run --script` and ephemeral
`uv run --with`. Each environment is tied to the exact Python interpreter that
created it. GitHub Actions lanes share cache keys across runs whose Python
patch versions can differ. On Windows, a restored environment can fail with
`did not find executable` before the action's script starts.

setup-uv's `prune-cache` input prunes before saving, but it does not remove an
environment restored earlier by a caller's setup-uv step. Both the archive and
restore paths therefore need a control.

## Decision

- Cache-writing setup-uv steps in uv-consuming lanes set `prune-cache: true`.
  This prevents interpreter-bound environments from being saved and moves the
  key lineage away from existing archives.
- Every direct workflow lane and composite action that runs uv invokes the
  shared `.github/scripts/prune_uv_cache.sh` after setup-uv and before its
  first uv command. The script removes stale environments in caller-restored
  caches while retaining reusable downloads.
- The script emits one fixed outcome metric,
  `uv-cache-prune.outcome=success|failure`, to the job log and summary. It
  returns uv's original non-zero status if pruning fails, so the metric does
  not hide the operational failure.
- `tests/workflows/test_uv_cache_prune.py` enforces setup, prune and first-uv
ordering across workflows and actions. Its isolated behaviour tests create an
ephemeral environment, prove pruning removes it, and check success and failure
reporting.

## Consequences

Script environments may be recreated on each runner, while package downloads
remain reusable. Pruning is an explicit blocking step before uv work. The
closed outcome metric identifies whether pruning succeeded without recording
cache paths or interpreter details.

## References

- [Developer guide](../developers-guide.md#interpreter-bound-uv-script-environments)
- [Workflow contract](../../tests/workflows/test_uv_cache_prune.py)
- [PR #637](https://github.com/leynos/shared-actions/pull/637)
