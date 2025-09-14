"""Unit tests for GoReleaser configuration."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_config_contains_arches() -> None:
    """Config lists paths for amd64 and arm64 artifacts."""
    cfg = Path(__file__).resolve().parents[4] / "rust-toy-app" / ".goreleaser.yaml"
    data = yaml.safe_load(cfg.read_text())
    builds = {b["id"]: b for b in data["builds"]}
    assert "prebuilt" in builds
    assert set(builds["prebuilt"]["goarch"]) == {"amd64", "arm64"}
    deb = next(n for n in data["nfpms"] if n["id"] == "deb")
    srcs = {c["src"] for c in deb["contents"]}
    assert "dist/{{ .ProjectName }}_{{ .Os }}_{{ .Arch }}/{{ .Binary }}" in srcs
