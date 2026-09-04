"""Shared accessors for the `install-tool` action and the tool manifest."""

from __future__ import annotations

import tomllib
import typing as typ
from pathlib import Path

import yaml

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"
RESOLVE_SCRIPT_PATH = ACTION_DIR / "scripts" / "resolve_tool.py"
ZIP_SCRIPT_PATH = ACTION_DIR / "scripts" / "extract_zip.py"
TOOL_MANIFEST_PATH = ACTION_DIR.parents[1] / "tool-manifest.toml"

#: The steps, in the order the action declares them. The order is the design:
#: resolution is pure and comes first, the probe decides whether anything else
#: runs, and the installed binary is asked for its version last.
STEP_NAMES = (
    "Resolve the tool entry",
    "Probe for an installed tool",
    "Download and verify the archive",
    "Extract and install the binary",
    "Verify the installed tool",
)

#: Runner pairs the action resolves, and the triple each maps to.
SUPPORTED_RUNNERS = {
    ("Linux", "X64"): "x86_64-unknown-linux-gnu",
    ("Linux", "ARM64"): "aarch64-unknown-linux-gnu",
    ("macOS", "X64"): "x86_64-apple-darwin",
    ("macOS", "ARM64"): "aarch64-apple-darwin",
    ("Windows", "X64"): "x86_64-pc-windows-msvc",
}

#: Every bounded metric the action emits, and the closed set each ranges over.
#: A widened vocabulary in the action without a widened one here makes a
#: scraper's series unbounded without anything failing.
METRICS = {
    "install-tool.resolve": {
        "ok",
        "no-python",
        "manifest-unreadable",
        "unknown-tool",
        "unknown-version",
        "unsupported-runner",
        "unsupported-target",
        "unsupported-extension",
    },
    "install-tool.sidecar-verified": {"true", "false", "absent"},
    "install-tool.cache": {"hit", "hit-unverified", "miss", "stale"},
    "install-tool.download": {"ok", "failed"},
    "install-tool.digest": {"verified", "mismatch"},
    "install-tool.install": {
        "ok",
        "failed",
        "missing-member",
        "unsupported-extension",
    },
    "install-tool.verify": {"ok", "mismatch", "missing", "unsupported"},
    "install-tool.result": {"installed", "cached"},
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
    message = f"missing install-tool step: {name}"
    raise AssertionError(message)


def load_tool_manifest() -> dict[str, typ.Any]:
    """Return the parsed tool manifest."""
    with TOOL_MANIFEST_PATH.open("rb") as handle:
        return tomllib.load(handle)


def manifest_entries() -> list[dict[str, typ.Any]]:
    """Return every tool entry in the manifest."""
    return load_tool_manifest()["tool"]


def manifest_targets() -> list[tuple[dict[str, typ.Any], dict[str, typ.Any]]]:
    """Return every (tool, target) pair in the manifest."""
    return [
        (entry, target) for entry in manifest_entries() for target in entry["target"]
    ]
