"""Tests for the payload staging helper script."""

from __future__ import annotations

import gzip
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_prepare_payload_stages_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Stage binaries, manpages, and licences into the pkgroot."""
    module = load_module("prepare_payload")
    monkeypatch.chdir(tmp_path)

    binary = tmp_path / "dist" / "mytool"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"#!/bin/sh\n")

    manpage = tmp_path / "docs" / "mytool.10"
    manpage.parent.mkdir(parents=True, exist_ok=True)
    manpage.write_text("manpage", encoding="utf-8")

    license_file = tmp_path / "LICENSE"
    license_file.write_text("license text", encoding="utf-8")

    module.main(
        name="mytool",
        install_prefix="/usr/local",
        binary=str(binary),
        manpage=str(manpage),
        license_file=str(license_file),
    )

    work_dir = tmp_path / ".macos-package"
    root = work_dir / "pkgroot"
    binary_dest = root / "usr/local/bin/mytool"
    manpage_dest = root / "usr/local/share/man/man10/mytool.10.gz"
    license_dest = root / "usr/local/share/doc/mytool/LICENSE"

    assert binary_dest.read_bytes() == b"#!/bin/sh\n"
    assert (binary_dest.stat().st_mode & 0o777) == 0o755

    with gzip.open(manpage_dest, "rt", encoding="utf-8") as handle:
        assert handle.read() == "manpage"
    assert (manpage_dest.stat().st_mode & 0o777) == 0o644

    assert license_dest.read_text(encoding="utf-8") == "license text"
    assert (license_dest.stat().st_mode & 0o777) == 0o644

    build_dir = work_dir / "build"
    resources_dir = work_dir / "Resources"
    assert build_dir.is_dir()
    assert resources_dir.is_dir()
    assert not (work_dir / "dist.xml").exists()


def test_prepare_payload_rejects_manpage_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Reject directories passed as the manpage path."""
    module = load_module("prepare_payload")
    monkeypatch.chdir(tmp_path)

    binary = tmp_path / "bin"
    binary.write_text("bin", encoding="utf-8")
    man_dir = tmp_path / "docs" / "man"
    man_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(module.ActionError):
        module.main(
            name="mytool",
            install_prefix="/usr/local",
            binary=str(binary),
            manpage=str(man_dir),
        )


def test_prepare_payload_rejects_license_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Reject directories supplied as the licence path."""
    module = load_module("prepare_payload")
    monkeypatch.chdir(tmp_path)

    binary = tmp_path / "bin"
    binary.write_text("bin", encoding="utf-8")
    license_dir = tmp_path / "docs" / "lic"
    license_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(module.ActionError):
        module.main(
            name="mytool",
            install_prefix="/usr/local",
            binary=str(binary),
            license_file=str(license_dir),
        )


def test_man_section_handles_multi_digit_sections(
    load_module: cabc.Callable[[str], object],
) -> None:
    """Infer the correct man section even when it has multiple digits."""
    module = load_module("prepare_payload")
    assert module._man_section(Path("mytool.10")) == "10"
    assert module._man_section(Path("mytool")) == "1"


def test_normalise_prefix_rejects_escape(
    tmp_path: Path, load_module: cabc.Callable[[str], object]
) -> None:
    """Prevent the install prefix from escaping the pkgroot."""
    module = load_module("prepare_payload")
    root = tmp_path / "pkgroot"
    root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(module.ActionError):
        module._normalise_prefix(root, "../../etc")
