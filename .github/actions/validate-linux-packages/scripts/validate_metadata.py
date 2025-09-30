"""Metadata inspection helpers for Debian and RPM packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from plumbum.commands.base import BaseCommand

from validate_commands import run_text

__all__ = [
    "DebMetadata",
    "RpmMetadata",
    "inspect_deb_package",
    "inspect_rpm_package",
]


@dataclass(slots=True)
class DebMetadata:
    """Metadata extracted from a Debian package."""

    name: str
    version: str
    architecture: str
    files: set[str]


@dataclass(slots=True)
class RpmMetadata:
    """Metadata extracted from an RPM package."""

    name: str
    version: str
    release: str
    architecture: str
    files: set[str]


_KV_SEPARATOR: Final[str] = ":"


def _parse_kv_output(text: str) -> dict[str, str]:
    """Return ``key: value`` lines from ``text`` as a dictionary."""

    entries: dict[str, str] = {}
    for line in text.splitlines():
        if _KV_SEPARATOR not in line:
            continue
        key, value = line.split(_KV_SEPARATOR, 1)
        entries[key.strip()] = value.strip()
    return entries


def _parse_dpkg_listing(output: str) -> set[str]:
    """Return payload paths from ``dpkg-deb -c`` output."""

    files: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=5)
        path = parts[-1] if parts else ""
        if not path:
            continue
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/"):
            files.add(path)
        else:
            files.add(f"/{path}")
    return files


def inspect_deb_package(dpkg_deb: BaseCommand, package_path: Path) -> DebMetadata:
    """Return metadata for ``package_path`` using ``dpkg-deb``."""

    info_output = run_text(
        dpkg_deb[
            "-f",
            package_path.as_posix(),
            "Package",
            "Version",
            "Architecture",
        ]
    )
    info = _parse_kv_output(info_output)
    listing_output = run_text(dpkg_deb["-c", package_path.as_posix()])
    return DebMetadata(
        name=info.get("Package", ""),
        version=info.get("Version", ""),
        architecture=info.get("Architecture", ""),
        files=_parse_dpkg_listing(listing_output),
    )


def _parse_rpm_listing(output: str) -> set[str]:
    """Return payload paths from ``rpm -qlp`` output."""

    files: set[str] = set()
    for line in output.splitlines():
        entry = line.strip()
        if entry:
            files.add(entry)
    return files


def inspect_rpm_package(rpm_cmd: BaseCommand, package_path: Path) -> RpmMetadata:
    """Return metadata for ``package_path`` using ``rpm``."""

    info_output = run_text(rpm_cmd["-qip", package_path.as_posix()])
    info = _parse_kv_output(info_output)
    listing_output = run_text(rpm_cmd["-qlp", package_path.as_posix()])
    return RpmMetadata(
        name=info.get("Name", ""),
        version=info.get("Version", ""),
        release=info.get("Release", ""),
        architecture=info.get("Architecture", ""),
        files=_parse_rpm_listing(listing_output),
    )
