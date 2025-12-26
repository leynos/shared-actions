"""Environment helpers shared by the staging toolchain."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import StageError

__all__ = ["require_env_path"]


def require_env_path(name: str) -> Path:
    """Return ``Path`` value for ``name`` or raise :class:`StageError`.

    Parameters
    ----------
    name
        Name of the environment variable to fetch.

    Returns
    -------
    Path
        The path value from the environment variable.

    Raises
    ------
    StageError
        Raised when the environment variable is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        msg = f"Environment variable '{name}' is not set."
        raise StageError(msg)
    return Path(value)
