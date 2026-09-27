#!/usr/bin/env bash
# Assert that a build actually put an object into the sccache backend.
#
# Compile requests and a zero write-error count prove less than they look.
# A hello-world binary produces four rustc invocations and sccache declines
# every one of them: two for missing inputs, one unclassified, and one for the
# crate type, because the final invocation of a `bin` crate links and sccache
# does not cache links. The result is four requests, zero misses, zero writes
# and zero write errors, which reads like success and is not one.
#
# So the probe is a library crate, whose compilation sccache does cache, and
# the assertion is on a cache miss followed by no write error: a miss is a
# write attempt, and only a write attempt can fail against the wrong endpoint.
#
# $1 the sccache binary, $2 the substring the cache location must contain.
# $3, optional, is `--expect-hit-on-rebuild`: clean the probe and build it
# again, and require a cache hit. A hit on the rebuild can only come from the
# backend, since the clean removed the local object, so it proves the read
# path as the miss proves the write path.
set -euo pipefail

sccache="${1:?path to sccache required}"
expected_location="${2:?expected cache location substring required}"
rebuild_mode="${3:-}"
case "$rebuild_mode" in
  ''|--expect-hit-on-rebuild) ;;
  *) echo "::error::unknown option: ${rebuild_mode}" >&2; exit 2 ;;
esac

probe="$(mktemp -d)"
mkdir -p "${probe}/src"
cat > "${probe}/Cargo.toml" <<'TOML'
[package]
name = "sccache-probe"
version = "0.0.0"
edition = "2021"

[lib]
path = "src/lib.rs"
TOML
# Enough of a function that rustc has something to compile, and no
# dependencies, so the build is a single cacheable unit.
cat > "${probe}/src/lib.rs" <<'RUST'
pub fn probe(left: u64, right: u64) -> u64 {
    left.wrapping_mul(right).rotate_left(7)
}
RUST

cargo build --manifest-path "${probe}/Cargo.toml"

stats="$("$sccache" --show-stats)"
printf '%s\n' "$stats"

field() {
  printf '%s\n' "$stats" | awk -v key="$1" '$0 ~ "^" key "  " {print $NF; exit}'
}

location="$(printf '%s\n' "$stats" | awk -F'  +' '/^Cache location/ {print $2; exit}')"
misses="$(field 'Cache misses')"
non_cacheable="$(field 'Non-cacheable compilations')"
write_errors="$(field 'Cache write errors')"
read_errors="$(field 'Cache read errors')"

echo "cache location: ${location}"
echo "misses=${misses:-0} non-cacheable=${non_cacheable:-0}" \
  "write errors=${write_errors:-0} read errors=${read_errors:-0}"

fail() {
  echo "::error::$1" >&2
  exit 1
}

case "$location" in
  *"$expected_location"*) ;;
  *) fail "sccache is not on the expected backend: ${location}" ;;
esac

# A miss is what a write attempt looks like from the client side. Without one
# the write-error count below is vacuous.
if [[ "${misses:-0}" -lt 1 ]]; then
  fail "sccache recorded no cache miss, so nothing was written to the backend"
fi
if [[ "${non_cacheable:-0}" -gt 0 ]]; then
  fail "sccache declined ${non_cacheable} compilation(s) as non-cacheable"
fi
if [[ "${write_errors:-0}" -gt 0 ]]; then
  fail "sccache reported ${write_errors} cache write error(s)"
fi
if [[ "${read_errors:-0}" -gt 0 ]]; then
  fail "sccache reported ${read_errors} cache read error(s)"
fi

echo "a cacheable compilation was written to ${expected_location} without error"

if [[ -z "$rebuild_mode" ]]; then
  exit 0
fi

cargo clean --manifest-path "${probe}/Cargo.toml"
cargo build --manifest-path "${probe}/Cargo.toml"
stats="$("$sccache" --show-stats)"
printf '%s\n' "$stats"
hits="$(field 'Cache hits')"
echo "hits after the rebuild=${hits:-0}"
if [[ "${hits:-0}" -lt 1 ]]; then
  fail "the rebuild recorded no cache hit, so nothing was read back from ${expected_location}"
fi
echo "the rebuild was served from ${expected_location}"
