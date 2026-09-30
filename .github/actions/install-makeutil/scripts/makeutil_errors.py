"""Exceptions `install_makeutil.py` and `makeutil_verify.py` raise on purpose.

Kept in their own module so neither of those two - the resolve-phase logic
and the download/verify/install phase - needs to import the other just to
catch the errors it raises.
"""

from __future__ import annotations


class MakeutilError(Exception):
    """Base class for every error this action's scripts raise deliberately."""


class InvalidInputError(MakeutilError):
    """Raised when a caller-supplied input fails validation."""


class UnsupportedPlatformError(MakeutilError):
    """Raised when the runner's OS/architecture has no published asset."""


class UnknownVersionError(MakeutilError):
    """Raised when the digest table has no entry for a requested version."""


class SidecarError(MakeutilError):
    """Raised when a `.sha256` sidecar is malformed or names another file."""


class DownloadError(MakeutilError):
    """Raised when a download could not be completed."""


class StoreError(MakeutilError):
    """Raised when the installed binary cannot be read, staged or removed."""
