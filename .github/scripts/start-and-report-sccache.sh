#!/usr/bin/env bash
# Test scaffolding for test-ubicloud-sccache-proxy.yml.
#
# Starts an sccache server from whichever position calls this and reports the
# backend it bound. sccache reads its cache configuration once, at server
# start, so the position of this call is the whole measurement.
set -euo pipefail

position="${1:?position label required}"

sccache --stop-server >/dev/null 2>&1 || true
rm -f "${SCCACHE_ERROR_LOG:-/dev/null}" 2>/dev/null || true
sccache --start-server
echo "--- sccache --show-stats after starting from ${position} ---"
sccache --show-stats
if [[ -n "${SCCACHE_ERROR_LOG:-}" && -f "${SCCACHE_ERROR_LOG}" ]]; then
  echo "--- sccache server log (${position}) ---"
  sed -n '1,60p' "${SCCACHE_ERROR_LOG}"
fi
sccache --stop-server >/dev/null 2>&1 || true
