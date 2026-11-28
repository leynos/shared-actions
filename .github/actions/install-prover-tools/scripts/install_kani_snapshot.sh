#!/usr/bin/env bash
# Install and check the Kani snapshot a consumer pins.
#
# Reads SOURCE (the pinned `uv tool run --from` source), ROOT (the consumer
# repository root) and PIN (the pin file) from the environment. `gh`, which the
# tool shells out to, reads GH_TOKEN from the environment; this script never
# prints it and never enables shell tracing.
set -euo pipefail

: "${SOURCE:?SOURCE is required}"
: "${ROOT:?ROOT is required}"
: "${PIN:?PIN is required}"

# The tool reads defaults for its flags from these names, and a caller's job
# environment may set them; every value needed is passed explicitly.
for name in $(compgen -e | grep -E '^INPUT_' || true); do unset "$name"; done
unset KANI KANI_CACHE_DIR KANI_INSTALL_DIR

prover() { uv tool run --from "$SOURCE" prover-tools "$@"; }

# A revision from before snapshots has no such subcommand; say what to change
# rather than failing inside the installer. Any other failure of the probe (a
# network or tool-start fault) is shown as the tool reported it.
if ! probe="$(prover kani snapshot --help 2>&1)"; then
  printf '%s\n' "$probe" >&2
  if printf '%s' "$probe" | grep -q 'Unknown command'; then
    echo "::error title=install-prover-tools::the prover-tools revision in" \
      "prover-tools-sha has no 'kani snapshot' command, which tools/kani/snapshot.toml" \
      "needs; set prover-tools-sha to f3ac307ef81b3af63f56c7e4a4191e566314e242 or later" >&2
  else
    echo "::error title=install-prover-tools::could not run prover-tools" \
      "(see the output above)" >&2
  fi
  exit 1
fi

# The default cache, `.kani-snapshots` under the repository root, is where
# `prover-tools kani run --snapshot-pin` looks, so none is named.
# The tool's own errors may not name the pin, so a failure here does.
for step in install check; do
  if ! prover kani snapshot "$step" --repo-root "$ROOT" --pin "$PIN"; then
    echo "::error title=install-prover-tools::kani snapshot $step failed" \
      "for the pin $PIN" >&2
    exit 1
  fi
done
