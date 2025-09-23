"""Tests for the licence resource preparation helper."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_prepare_license_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Copy the licence text and render the Distribution XML."""
    module = load_module("prepare_license_resources")
    monkeypatch.chdir(tmp_path)

    license_file = tmp_path / "LICENSE.txt"
    license_file.write_text('Terms "and" conditions\n', encoding="utf-8")

    module.main(
        name="MyTool & Co.",
        identifier="com.example.tool",
        version="1.0.0",
        license_file=str(license_file),
    )

    work_dir = tmp_path / ".macos-package"
    resources = work_dir / "Resources"
    dist_xml = work_dir / "dist.xml"
    copied_license = Path(resources / "License.txt")

    assert copied_license.read_text(encoding="utf-8") == 'Terms "and" conditions\n'
    assert (copied_license.stat().st_mode & 0o777) == 0o644

    xml_content = dist_xml.read_text(encoding="utf-8")
    assert "MyTool &amp; Co." in xml_content
    assert "com.example.tool" in xml_content


def test_prepare_license_requires_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Reject missing or directory licence paths."""
    module = load_module("prepare_license_resources")
    monkeypatch.chdir(tmp_path)

    error_type = module.ensure_regular_file.__globals__["ActionError"]

    with pytest.raises(error_type):
        module.main(
            name="tool",
            identifier="com.example.tool",
            version="1.0.0",
            license_file=str(tmp_path / "missing"),
        )

    license_dir = tmp_path / "dir"
    license_dir.mkdir()
    with pytest.raises(error_type):
        module.main(
            name="tool",
            identifier="com.example.tool",
            version="1.0.0",
            license_file=str(license_dir),
        )
