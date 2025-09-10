#!/usr/bin/env bash
set -euo pipefail

target="${RBR_TARGET:-${1:-}}"
echo "::warning:: rust-build-release is a stub; target='${target}' not built"
exit 1
