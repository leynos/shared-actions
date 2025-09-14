"""Unit tests for GoReleaser configuration."""

from __future__ import annotations

from pathlib import Path


def test_config_contains_arches() -> None:
    """Config lists builds for amd64 and arm64."""
    cfg = Path(__file__).resolve().parents[4] / "rust-toy-app" / ".goreleaser.yaml"
    text = cfg.read_text()
    assert "prebuilt-amd64" in text
    assert "prebuilt-arm64" in text
    assert "rust-toy-app_linux_{{ .Arch }}" in text
