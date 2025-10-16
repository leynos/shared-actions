"""Shared helpers for validate-linux-packages tests."""

from __future__ import annotations

import typing as typ

from test_support.sandbox import DummySandbox

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

PackageMetadataModule = typ.Any


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
    module: PackageMetadataModule,
    *,
    name: str = "rust-toy-app",
    version: str = "1.2.3-1",
    architecture: str = "amd64",
    files: typ.Iterable[str] | None = None,
) -> object:
    """Construct ``DebMetadata`` instances for tests."""
    payload = set(files or {"/usr/bin/rust-toy-app"})
    return module.DebMetadata(
        name=name,
        version=version,
        architecture=architecture,
        files=payload,
    )


def build_rpm_metadata(
    module: PackageMetadataModule,
    *,
    name: str = "rust-toy-app",
    version: str = "1.2.3",
    release: str = "1.el9",
    architecture: str = "x86_64",
    files: typ.Iterable[str] | None = None,
) -> object:
    """Construct ``RpmMetadata`` instances for tests."""
    payload = set(files or {"/usr/bin/rust-toy-app"})
    return module.RpmMetadata(
        name=name,
        version=version,
        release=release,
        architecture=architecture,
        files=payload,
    )
