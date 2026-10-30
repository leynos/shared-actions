#!/usr/bin/env bash
# Deny github.com after the install, then prove the denial. A positive control
# comes first: the canary must succeed before the denial, or a network fault
# would pass for a working block. Only then is the host denied, and the canary
# must fail. The action's own downloads have finished by now; only the driver
# build is being starved of public Clippy.
set -euo pipefail
canary() {
  timeout 30 git ls-remote https://github.com/rust-lang/rust-clippy HEAD >/dev/null 2>&1
}
canary || { echo "positive control failed: github.com was unreachable before the denial, so the denial proves nothing" >&2; exit 1; }
echo "0.0.0.0 github.com" | sudo tee -a /etc/hosts >/dev/null
if canary; then
  echo "the github.com canary still succeeds; the denial did not take effect" >&2
  exit 1
fi
echo "github.com is unreachable; public Clippy cannot be read."
