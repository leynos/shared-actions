#!/usr/bin/env bash
# Pure Clippy-snapshot resolution for the install-whitaker action.
#
# Reads the installer version, the snapshot table, the pinned bundle digests
# and the caller's optional digest, and prints the resolved contract as
# `key=value` lines. Like resolve-release.sh it performs no externally visible
# effect and reports an expected failure as a printed record, not an exit code;
# the action's adapter step publishes it.
#
# Records:
#   status=none      the installer predates the bootstrap (no snapshot)
#   status=install   bundle-asset, sidecar-asset, commit, dylint-version,
#                    toolchain, expected-sha, trust-anchor
#   status=error     error-kind, error-message
set -uo pipefail

error() {
  printf 'status=error\nerror-kind=%s\nerror-message=%s\n' "$1" "$2"
}

# Succeeds when $1 (a dotted version) is at least 0.2.11, the first installer
# that provisions a snapshot and bootstraps the driver.
has_bootstrap() {
  local major minor patch
  IFS=. read -r major minor patch <<< "$1"
  minor="${minor:-0}"
  patch="${patch:-0}"
  (( major > 0 || minor > 2 || (minor == 2 && patch >= 11) ))
}

resolve_snapshot() {
  local version="$WHITAKER_INSTALLER_VERSION" table row commit dylint toolchain
  local bundle pinned supplied
  if ! has_bootstrap "$version"; then
    printf 'status=none\n'
    return 0
  fi
  table="${WHITAKER_SNAPSHOT_TABLE:-}"
  if [[ ! -r "$table" ]]; then
    error table-unreadable "could not read the Clippy snapshot table at ${table}"
    return 0
  fi
  if ! row="$(awk -v v="$version" '$1 == v { print; exit }' "$table")"; then
    error table-unreadable "could not read the Clippy snapshot table at ${table}"
    return 0
  fi
  if [[ -z "$row" ]]; then
    error unlisted-version "no Clippy snapshot is recorded for whitaker-installer ${version}; it cannot be installed with a verified bootstrap"
    return 0
  fi
  read -r _ commit dylint toolchain <<< "$row"
  if [[ ! "$commit" =~ ^[0-9a-f]{40}$ || -z "$dylint" || -z "$toolchain" ]]; then
    error table-invalid "the Clippy snapshot row for ${version} is malformed"
    return 0
  fi
  bundle="whitaker-clippy-snapshot-${commit}.bundle"
  if [[ ! -r "${WHITAKER_SNAPSHOT_DIGEST_MANIFEST:-}" ]]; then
    error manifest-unreadable "could not read the snapshot digest manifest at ${WHITAKER_SNAPSHOT_DIGEST_MANIFEST:-}"
    return 0
  fi
  if ! pinned="$(awk -v a="$bundle" '$2 == a { print $1; exit }' "$WHITAKER_SNAPSHOT_DIGEST_MANIFEST")"; then
    error manifest-unreadable "could not read the snapshot digest manifest at ${WHITAKER_SNAPSHOT_DIGEST_MANIFEST}"
    return 0
  fi
  supplied="${WHITAKER_SNAPSHOT_SHA256:-}"
  if [[ -n "$pinned" && -n "$supplied" && "$pinned" != "$supplied" ]]; then
    error digest-conflict "clippy-snapshot-sha256 ${supplied} conflicts with the digest pinned for ${bundle} (${pinned}); remove the input or correct it"
    return 0
  fi
  if [[ -z "$pinned" && -z "$supplied" ]]; then
    error unpinned-digest "no pinned SHA-256 for ${bundle}; supply the clippy-snapshot-sha256 input to install version ${version}"
    return 0
  fi
  printf 'status=install\n'
  printf 'bundle-asset=%s\n' "$bundle"
  printf 'sidecar-asset=%s.sha256\n' "$bundle"
  printf 'commit=%s\n' "$commit"
  printf 'dylint-version=%s\n' "$dylint"
  printf 'toolchain=%s\n' "$toolchain"
  printf 'expected-sha=%s\n' "${pinned:-$supplied}"
  printf 'trust-anchor=%s\n' "$([[ -n "$pinned" ]] && echo pinned || echo input)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  resolve_snapshot
fi
