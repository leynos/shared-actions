#!/usr/bin/env bash
# Pure release resolution for the install-whitaker action.
#
# Reads the runner pair, the pinned digest manifest, the caller's requested
# version and optional digest, and the state of any cached installer, then
# prints the resolved contract as `key=value` lines on stdout.
#
# This file performs no externally visible effect. It writes no file, emits no
# job-summary metric, prints no workflow annotation, and reports an expected
# resolution failure as a printed record rather than by exiting non-zero. The
# action's adapter step captures this output and publishes it. Keeping the two
# apart lets the tests exercise the query directly.
#
# `errexit` is deliberately absent: a resolution failure is a result, not a
# crash, and the adapter step owns the ERR trap for genuine internal failures.
set -uo pipefail

# Print "<target> <extension> <installer-name>" for a supported runner pair,
# and nothing for an unsupported one. An unsupported pair is an expected
# result, so this must not return non-zero: the caller's `errtrace` would
# otherwise run its ERR trap inside the command substitution.
resolve_target() {
  case "${RUNNER_OPERATING_SYSTEM}:${RUNNER_ARCHITECTURE}" in
    Linux:X64) echo 'x86_64-unknown-linux-gnu tgz whitaker-installer' ;;
    Linux:ARM64) echo 'aarch64-unknown-linux-gnu tgz whitaker-installer' ;;
    macOS:X64) echo 'x86_64-apple-darwin tgz whitaker-installer' ;;
    macOS:ARM64) echo 'aarch64-apple-darwin tgz whitaker-installer' ;;
    Windows:X64) echo 'x86_64-pc-windows-msvc zip whitaker-installer.exe' ;;
    *) ;;
  esac
}

# Print the digest pinned for an asset, or nothing when it is not pinned.
#
# An absent manifest means nothing is pinned, which is a result. A manifest
# that exists but cannot be read is a failure, and must stay one: degrading it
# to "not pinned" would silently fall back to the caller's digest, or report an
# unpinned asset, when the trust anchor was merely unreadable.
pinned_digest() {
  local manifest="${WHITAKER_DIGEST_MANIFEST:-}"
  if [[ ! -f "$manifest" ]]; then
    return 0
  fi
  if [[ ! -r "$manifest" ]]; then
    return 2
  fi
  awk -v asset="$1" '$2 == asset { print $1; exit }' "$manifest" || return 2
}

# Print the version recorded beside a cached installer, or nothing.
#
# As above, an absent marker is a result and an unreadable one is a failure.
cached_installer_version() {
  local marker="${WHITAKER_INSTALLER_VERSION_PATH:-}"
  if [[ ! -f "$marker" ]]; then
    return 0
  fi
  if [[ ! -r "$marker" ]]; then
    return 2
  fi
  awk 'NR == 1 { print $1; exit }' "$marker" || return 2
}

# Print the trust anchor for an asset as "<digest> <source>", or an error
# record when the manifest and the supplied digest cannot agree on one.
resolve_trust_anchor() {
  local asset="$1" pinned_sha supplied_sha
  if ! pinned_sha="$(pinned_digest "$asset")"; then
    printf 'status=error\nerror-kind=manifest-unreadable\nerror-message=%s\n' \
      "could not read the pinned digest manifest at ${WHITAKER_DIGEST_MANIFEST}"
    return 1
  fi
  supplied_sha="${WHITAKER_INSTALLER_SHA256:-}"
  if [[ -n "$pinned_sha" ]]; then
    if [[ -n "$supplied_sha" && "$supplied_sha" != "$pinned_sha" ]]; then
      printf 'status=error\nerror-kind=digest-conflict\nerror-message=%s\n' \
        "installer-sha256 ${supplied_sha} conflicts with the digest pinned for ${asset} (${pinned_sha}); remove the input or correct it"
      return 1
    fi
    printf '%s pinned\n' "$pinned_sha"
    return 0
  fi
  if [[ -n "$supplied_sha" ]]; then
    printf '%s input\n' "$supplied_sha"
    return 0
  fi
  printf 'status=error\nerror-kind=unpinned-digest\nerror-message=%s\n' \
    "no pinned SHA-256 for ${asset}; supply the installer-sha256 input to install version ${WHITAKER_INSTALLER_VERSION}"
  return 1
}

# Print the whole resolved contract.
resolve_release() {
  local target extension installer_name target_spec
  local asset anchor expected_sha trust_anchor cached_version
  # The runner must be supported before anything else is considered. Reusing a
  # cached installer on an unsupported runner would report success for a
  # platform this action cannot install, and would hide the misconfiguration
  # for as long as the cache survives.
  target_spec="$(resolve_target)"
  if [[ -z "$target_spec" ]]; then
    printf 'status=error\nerror-kind=unsupported-runner\nerror-message=%s\n' \
      "unsupported runner ${RUNNER_OPERATING_SYSTEM}/${RUNNER_ARCHITECTURE}"
    return 0
  fi
  read -r target extension installer_name <<< "$target_spec"
  if [[ -x "$WHITAKER_INSTALLER_PATH" ]]; then
    if ! cached_version="$(cached_installer_version)"; then
      printf 'status=error\nerror-kind=version-marker-unreadable\nerror-message=%s\n' \
        "could not read the installed-version marker at ${WHITAKER_INSTALLER_VERSION_PATH}"
      return 0
    fi
    if [[ "$cached_version" == "$WHITAKER_INSTALLER_VERSION" ]]; then
      printf 'status=cached\n'
      return 0
    fi
    printf 'stale-version=%s\n' "${cached_version:-unknown}"
  fi
  asset="whitaker-installer-${target}-v${WHITAKER_INSTALLER_VERSION}.${extension}"
  if ! anchor="$(resolve_trust_anchor "$asset")"; then
    printf '%s\n' "$anchor"
    return 0
  fi
  read -r expected_sha trust_anchor <<< "$anchor"
  printf 'status=install\n'
  printf 'asset=%s\n' "$asset"
  printf 'extension=%s\n' "$extension"
  printf 'installer-name=%s\n' "$installer_name"
  printf 'expected-sha=%s\n' "$expected_sha"
  printf 'trust-anchor=%s\n' "$trust_anchor"
  printf 'staging-dir=%s\n' "$WHITAKER_STAGING_DIR"
}

# Run only when executed, so a test may source the file for one helper.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  resolve_release
fi
