"""Package discovery utilities for locating Debian and RPM packages."""

from __future__ import annotations

import pathlib
import typing as typ

from validate_exceptions import ValidationError
from validate_helpers import unique_match

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
else:  # pragma: no cover - runtime fallback
    Path = pathlib.Path

__all__ = [
    "ensure_subset",
    "locate_deb",
    "locate_rpm",
]


def locate_deb(
    package_dir: Path, package_name: str, version: str, release: str
) -> Path:
    """Return the Debian package matching ``package_name`` and ``version``."""
    pattern = f"{package_name}_{version}-{release}_*.deb"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} deb package"
    )


def locate_rpm(
    package_dir: Path, package_name: str, version: str, release: str
) -> Path:
    """Return the RPM package matching ``package_name`` and ``version``."""
    pattern = f"{package_name}-{version}-{release}*.rpm"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} rpm package"
    )


def ensure_subset(
    expected: typ.Collection[str], actual: typ.Collection[str], label: str
) -> None:
    """Raise :class:`ValidationError` when expected items are missing."""
    if missing := [path for path in expected if path not in actual]:
        message = f"missing {label}: {', '.join(missing)}"
        raise ValidationError(message)
