#!/usr/bin/env bash
# Where whitaker-installer 0.2.11 keeps its Dylint bootstrap, and how to read
# the installer's own report of it.
#
#   bootstrap-paths.sh root <os>   print the bootstrap directory the installer
#                                  will use on a runner of that OS, before it
#                                  has run (its `--dry-run` cannot run first:
#                                  it needs the managed clone)
#   bootstrap-paths.sh parse       read a `--dry-run` report on stdin and print
#                                  `generation=`, `state=` and `driver-directory=`
#
# The installer sits the bootstrap beside its data directory, from the
# `directories-next` project directories for qualifier "io", organisation
# "github" and application "whitaker" (the data directory's parent):
# `$XDG_DATA_HOME` or `~/.local/share` on Linux, `~/Library/Application
# Support` on macOS, and `%APPDATA%\github\whitaker` on Windows.
set -euo pipefail

root_for() {
  case "$1" in
    Linux) printf '%s/whitaker-bootstrap\n' "${XDG_DATA_HOME:-${HOME}/.local/share}" ;;
    macOS) printf '%s/Library/Application Support/whitaker-bootstrap\n' "$HOME" ;;
    Windows) printf '%s\\github\\whitaker\\whitaker-bootstrap\n' "${APPDATA:?APPDATA must be set on Windows}" ;;
    *) echo "unsupported runner OS $1" >&2; return 1 ;;
  esac
}

parse_report() {
  local report generation state driver
  report="$(tr -d '\r')"
  label() { printf '%s\n' "$report" | sed -n "s/^$1: //p" | head -n 1; }
  generation="$(label 'Clippy snapshot')"
  state="$(label 'Clippy snapshot state')"
  driver="$(label 'Dylint driver directory')"
  if [[ -z "$generation" || -z "$driver" ]]; then
    echo "the installer's dry run did not name a Clippy snapshot and a Dylint driver directory" >&2
    return 1
  fi
  printf 'generation=%s\nstate=%s\ndriver-directory=%s\n' "$generation" "$state" "$driver"
}

case "${1:-}" in
  root) root_for "${2:?runner OS}" ;;
  parse) parse_report ;;
  *) echo "usage: $0 root <os> | parse" >&2; exit 2 ;;
esac
