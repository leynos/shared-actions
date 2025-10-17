"""Shared helpers for validate-linux-packages tests."""

from __future__ import annotations

import dataclasses
import typing as typ

from ..sandbox import DummySandbox  # noqa: TID252

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

PackageMetadataModule = typ.Any


@dataclasses.dataclass
class DebPackageParams:
    """Parameters for constructing Debian package metadata in tests."""

    name: str = "rust-toy-app"
    version: str = "1.2.3-1"
    architecture: str = "amd64"
    files: typ.Iterable[str] | None = None


def write_package(tmp_path: Path, filename: str, content: bytes = b"payload") -> Path:
    """Create a package file under ``tmp_path`` with the given ``filename``."""
    path = tmp_path / filename
    path.write_bytes(content)
    return path


def make_dummy_sandbox(
    tmp_path: Path, calls: list[tuple[tuple[str, ...], int | None]]
) -> DummySandbox:
    """Return a ``DummySandbox`` rooted under ``tmp_path`` recording ``calls``."""
    return DummySandbox(tmp_path / "sandbox", calls)


def build_deb_metadata(
    module: PackageMetadataModule, params: DebPackageParams | None = None
) -> object:
    """Construct ``DebMetadata`` instances for tests."""
    if params is None:
        params = DebPackageParams()
    payload = set(params.files or {"/usr/bin/rust-toy-app"})
    return module.DebMetadata(
        name=params.name,
        version=params.version,
        architecture=params.architecture,
        files=payload,
    )


@dataclasses.dataclass
class RpmPackageParams:
    """Parameters for constructing RPM package metadata in tests."""

    name: str = "rust-toy-app"
    version: str = "1.2.3"
    release: str = "1.el9"
    architecture: str = "x86_64"
    files: typ.Iterable[str] | None = None


def build_rpm_metadata(
    module: PackageMetadataModule, params: RpmPackageParams | None = None
) -> object:
    """Construct ``RpmMetadata`` instances for tests."""
    if params is None:
        params = RpmPackageParams()
    payload = set(params.files or {"/usr/bin/rust-toy-app"})
    return module.RpmMetadata(
        name=params.name,
        version=params.version,
        release=params.release,
        architecture=params.architecture,
        files=payload,
    )
