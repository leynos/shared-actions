#!/usr/bin/env bash

set -euo pipefail

emit_metric() {
  local outcome="$1"
  local metric="uv-cache-prune.outcome=${outcome}"

  printf 'metric %s\n' "$metric"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf 'metric %s\n' "$metric" >> "$GITHUB_STEP_SUMMARY"
  fi
}

if uv cache prune; then
  emit_metric success
else
  uv_status=$?
  if ! emit_metric failure; then
    printf '%s\n' 'Could not write the uv cache prune failure metric.' >&2
  fi
  exit "$uv_status"
fi
