"""Tests for the setup-rust reference in rust-build-release."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


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
    expected_pattern = (
        r"leynos/shared-actions/\.github/actions/setup-rust@([0-9a-f]{40})"
    )
    match = re.fullmatch(expected_pattern, uses)
    assert match is not None, f"Expected SHA-pinned reference, got: {uses}"
    sha = match.group(1)
    assert len(sha) == 40, f"SHA must be exactly 40 hex characters, got: {sha}"


def test_setup_rust_step_includes_tag_comment() -> None:
    """The manifest should annotate the pinned SHA with the tag name."""
    manifest_text = ACTION_PATH.read_text(encoding="utf-8")
    assert "# setup-rust-v1" in manifest_text
