#!/usr/bin/env bash
# Lint a crate that deliberately calls std::fs through the installed wrapper.
#
# Usage: lint-fixture.sh cold|restored|missing
#
#   cold      the driver is built on this run, once; a second run on the same
#             machine reuses it byte for byte and prints no build notice
#   restored  the bootstrap came from the cache. Whitaker marks a driver ready
#             by its file identity (device, inode, creation time), which a
#             restore cannot preserve, so the driver is rebuilt once, from the
#             restored and verified snapshot, with github.com denied
#   missing   the snapshot generation is deleted first; the wrapper must fail
#             clearly, naming the snapshot, and must not report the diagnostic
#
# Every lint failure has to name the diagnostic: a crash or a missing driver
# never counts as enforcement. RUSTFLAGS carries -D warnings, as consumers' do.
set -euo pipefail
mode=${1:?mode}
diagnostic=${WHITAKER_TEST_DIAGNOSTIC:-no_std_fs_operations}
notice="built the Dylint driver on first use"
work="${RUNNER_TEMP:-/tmp}/whitaker-fixture"
rm -rf -- "$work"
mkdir -p "$work/src"
cat > "$work/Cargo.toml" <<'TOML'
[package]
name = "whitaker_bootstrap_fixture"
version = "0.1.0"
edition = "2021"

[workspace]
TOML
cat > "$work/src/lib.rs" <<'RUST'
//! Deliberately bypasses the capability-based filesystem policy.

/// Read a file through ambient `std::fs`, which Whitaker forbids.
pub fn read_config() -> String {
    std::fs::read_to_string("config.toml").unwrap_or_default()
}
RUST
driver_sha256() {
  local driver
  driver=$(find "${DRIVER_DIRECTORY:?}" -type f -name dylint-driver | head -n 1)
  [[ -n "$driver" ]] || { echo "no driver was built under ${DRIVER_DIRECTORY}" >&2; exit 1; }
  sha256sum "$driver" | cut -d ' ' -f 1
}
log="$work/lint.log"
lint() {
  local status=0
  (cd "$work" && RUSTFLAGS="-D warnings" whitaker --all) > "$log" 2>&1 || status=$?
  cat "$log"
  [[ "$status" -ne 0 ]] || { echo "whitaker succeeded on a std::fs violation" >&2; exit 1; }
}
builds() { grep -c -- "$notice" "$log" || true; }
if [[ "$mode" == missing ]]; then
  rm -rf -- "${SNAPSHOT:?}"
fi
lint
case "$mode" in
  missing)
    grep -qi "snapshot" "$log" || { echo "the missing snapshot was not named" >&2; exit 1; }
    if grep -q "$diagnostic" "$log"; then
      echo "a lint ran without its snapshot" >&2
      exit 1
    fi
    ;;
  cold)
    grep -q "$diagnostic" "$log" || { echo "no ${diagnostic} diagnostic" >&2; exit 1; }
    [[ "$(builds)" -eq 1 ]] || { echo "the cold run did not build the driver once" >&2; exit 1; }
    first="$(driver_sha256)"
    lint
    grep -q "$diagnostic" "$log" || { echo "no ${diagnostic} diagnostic on the warm rerun" >&2; exit 1; }
    [[ "$(builds)" -eq 0 ]] || { echo "the warm rerun rebuilt the driver" >&2; exit 1; }
    [[ "$(driver_sha256)" == "$first" ]] || { echo "the warm rerun replaced the driver" >&2; exit 1; }
    ;;
  restored)
    grep -q "$diagnostic" "$log" || { echo "no ${diagnostic} diagnostic" >&2; exit 1; }
    [[ "$(builds)" -eq 1 ]] || { echo "the restored bootstrap did not rebuild the driver once" >&2; exit 1; }
    ;;
  *) echo "unknown mode ${mode}" >&2; exit 2 ;;
esac
