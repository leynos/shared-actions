#!/usr/bin/env bash
# Assert that a build read back what an earlier run cached in SCCACHE_DIR.
#
# setup-rust restores the trunk's sccache directory before this runs. If that
# restore brought any object back, a build of the same crate on the same
# checkout path must record at least one cache hit; a restored directory that
# serves nothing is the failure this catches. If nothing was restored, the run
# is cold, which is expected until the default branch has saved once, and the
# script says so rather than failing.
#
# $1 the sccache binary, $2 the Cargo manifest to build.
set -euo pipefail

sccache="${1:?path to sccache required}"
manifest="${2:?Cargo manifest required}"
directory="${SCCACHE_DIR:?SCCACHE_DIR must name the restored directory}"

# Read before anything compiles, so only the restore can have filled it. On a
# cold run the directory does not exist yet, and find then exits non-zero,
# which `set -e` would turn into a failure; -quit also avoids the SIGPIPE a
# `| head` gets under pipefail once the directory holds many files.
restored=""
if [[ -d "$directory" ]]; then
  restored="$(find "$directory" -type f -print -quit)"
fi

cargo build --manifest-path "$manifest"
stats="$("$sccache" --show-stats)"
printf '%s\n' "$stats"
hits="$(printf '%s\n' "$stats" | awk '/^Cache hits  / {print $NF; exit}')"
location="$(printf '%s\n' "$stats" | awk -F'  +' '/^Cache location/ {print $2; exit}')"
# The counters belong to whoever reads them next. A crate with a build script
# and a binary always has non-cacheable compilations, which a later probe
# would otherwise count as its own.
"$sccache" --zero-stats >/dev/null

case "$location" in
  *"Local disk"*) ;;
  *) echo "::error::sccache is not on local disk: ${location}" >&2; exit 1 ;;
esac
if [[ -z "$restored" ]]; then
  echo "::notice title=sccache warm hit::nothing was restored, so this run is cold"
  exit 0
fi
if [[ "${hits:-0}" -lt 1 ]]; then
  echo "::error::a restored sccache directory served no cache hit" >&2
  exit 1
fi
echo "the restored directory served ${hits} cache hit(s)"
