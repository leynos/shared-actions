"""Tests for the setup-rust reference in rust-build-release."""

from __future__ import annotations

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
    """The setup-rust action should be referenced by a tagged ref."""
    step = _load_setup_rust_step()
    uses = step.get("uses")
    assert isinstance(uses, str)
    assert uses == "leynos/shared-actions/.github/actions/setup-rust@setup-rust-v1"
