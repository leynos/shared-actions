#!/usr/bin/env bash
# Test scaffolding for test-ubicloud-sccache-proxy.yml.
#
# Reports which cache variables the runner presents at one position in a job.
# The design in setup-rust rests on run steps reading GITHUB_ENV and the runner
# taking nothing back; this is what would notice that changing (#441).
# The proxy URL carries a bearer-like path segment and is masked as a secret,
# so only the host is printed; the host is what distinguishes Ubicloud's
# private proxy from GitHub's public endpoint, and it is not itself sensitive.
set -euo pipefail

position="${1:?position label required}"

state_of() {
  local name="$1"
  local value
  # Bash 3.2 has no nameref; eval is the portable indirect read here.
  eval "value=\${$name+set}"
  if [[ -z "${value:-}" ]]; then
    printf 'unset'
    return
  fi
  eval "value=\${$name}"
  if [[ -z "$value" ]]; then
    printf 'empty'
  else
    printf 'non-empty'
  fi
}

host_of() {
  local name="$1"
  local value
  eval "value=\${$name:-}"
  if [[ -z "$value" ]]; then
    printf 'none'
    return
  fi
  value="${value#*://}"
  printf '%s' "${value%%/*}"
}

echo "=== ${position} ==="
for name in ACTIONS_CACHE_SERVICE_V2 ACTIONS_CACHE_URL ACTIONS_RESULTS_URL \
  ACTIONS_RUNTIME_TOKEN SCCACHE_GHA_ENABLED; do
  echo "metric probe.${position}.${name}=$(state_of "$name")"
done
echo "metric probe.${position}.ACTIONS_CACHE_SERVICE_V2.value=${ACTIONS_CACHE_SERVICE_V2-<unset>}"
echo "metric probe.${position}.ACTIONS_CACHE_URL.host=$(host_of ACTIONS_CACHE_URL)"
echo "metric probe.${position}.ACTIONS_RESULTS_URL.host=$(host_of ACTIONS_RESULTS_URL)"
