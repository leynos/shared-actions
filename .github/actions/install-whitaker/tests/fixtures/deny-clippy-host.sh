#!/usr/bin/env bash
# Deny github.com after the install, then prove the denial with a canary, so a
# green lint cannot have cloned public Clippy. The action's own downloads have
# finished by now; only the driver build is being starved.
set -euo pipefail
canary() { git ls-remote https://github.com/rust-lang/rust-clippy HEAD >/dev/null 2>&1; }
if canary; then
  echo "0.0.0.0 github.com" | sudo tee -a /etc/hosts >/dev/null
fi
if canary; then
  echo "the github.com canary still succeeds; the denial did not take effect" >&2
  exit 1
fi
echo "github.com is unreachable; public Clippy cannot be read."
