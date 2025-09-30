"""Metadata inspection helpers for Debian and RPM packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, TypeVar

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
MetaT = TypeVar("MetaT")


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


def _inspect_package(
    info_cmd: BaseCommand,
    list_cmd: BaseCommand,
    *,
    list_parser: Callable[[str], set[str]],
    builder: Callable[[dict[str, str], set[str]], MetaT],
) -> MetaT:
    """Return package metadata using parameterised commands and parsers."""

    info_output = run_text(info_cmd)
    info = _parse_kv_output(info_output)
    listing_output = run_text(list_cmd)
    files = list_parser(listing_output)
    return builder(info, files)


def inspect_deb_package(dpkg_deb: BaseCommand, package_path: Path) -> DebMetadata:
    """Return metadata for ``package_path`` using ``dpkg-deb``."""

    return _inspect_package(
        dpkg_deb[
            "-f",
            package_path.as_posix(),
            "Package",
            "Version",
            "Architecture",
        ],
        dpkg_deb["-c", package_path.as_posix()],
        list_parser=_parse_dpkg_listing,
        builder=lambda info, files: DebMetadata(
            name=info.get("Package", ""),
            version=info.get("Version", ""),
            architecture=info.get("Architecture", ""),
            files=files,
        ),
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

    return _inspect_package(
        rpm_cmd["-qip", package_path.as_posix()],
        rpm_cmd["-qlp", package_path.as_posix()],
        list_parser=_parse_rpm_listing,
        builder=lambda info, files: RpmMetadata(
            name=info.get("Name", ""),
            version=info.get("Version", ""),
            release=info.get("Release", ""),
            architecture=info.get("Architecture", ""),
            files=files,
        ),
    )
