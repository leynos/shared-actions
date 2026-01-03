"""Tests for the setup-rust reference in rust-build-release."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
SETUP_RUST_SHA = "35b0092c30d18ba2669db3e8c7c37412db952437"


def _load_setup_rust_step() -> dict[str, object]:
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = manifest["runs"]["steps"]
    for step in steps:
        if step.get("name") == "Setup Rust toolchain":
            return step
    message = "Setup Rust toolchain step missing from action"
    raise AssertionError(message)


def test_setup_rust_step_uses_tagged_reference() -> None:
    """The setup-rust action should be pinned to a full commit SHA."""
    step = _load_setup_rust_step()
    uses = step.get("uses")
    assert isinstance(uses, str)
    assert uses == f"leynos/shared-actions/.github/actions/setup-rust@{SETUP_RUST_SHA}"
    assert re.fullmatch(r"[0-9a-f]{40}", SETUP_RUST_SHA) is not None


def test_setup_rust_step_includes_tag_comment() -> None:
    """The manifest should annotate the pinned SHA with the tag name."""
    manifest_text = ACTION_PATH.read_text(encoding="utf-8")
    assert "# setup-rust-v1" in manifest_text
