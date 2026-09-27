"""Shared accessors for the `install-makeutil` action manifest."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"
INSTALL_SCRIPT_PATH = ACTION_DIR / "scripts" / "install_makeutil.py"

#: Composite step names, in the order the action declares them: resolution
#: is pure and comes first, the cache is consulted next, installation
#: verifies and writes the binary, and PATH is extended last.
STEP_NAMES = (
    "Resolve the makeutil install plan",
    "Restore cached makeutil",
    "Install makeutil",
    "Add makeutil to PATH",
)

#: Every runner pair the action installs a prebuilt binary for.
SUPPORTED_TARGETS = {
    ("Linux", "X64"): "x86_64-unknown-linux-musl",
    ("Linux", "ARM64"): "aarch64-unknown-linux-musl",
}

#: The pinned `actions/cache` reference this repository already uses
#: elsewhere (for example `install-mdtablefix`'s README and
#: `generate-coverage`'s `action.yml`).
CACHE_ACTION_REF = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"

#: The bounded vocabulary the `install-makeutil.result` metric ranges over.
METRIC_RESULTS = {
    "invalid-input",
    "unsupported-platform",
    "unknown-version",
    "cached",
    "installed",
    "digest-mismatch",
    "sidecar-mismatch",
    "download-failed",
}


def load_action() -> dict[str, typ.Any]:
    """Return the parsed action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def action_steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's steps."""
    return load_action()["runs"]["steps"]


def step_by_name(name: str) -> dict[str, typ.Any]:
    """Return a named step, failing clearly when it has been renamed."""
    for step in action_steps():
        if step.get("name") == name:
            return step
    message = f"missing install-makeutil step: {name}"
    raise AssertionError(message)
