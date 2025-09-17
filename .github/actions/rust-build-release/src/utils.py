"""Utility helpers for validating executables."""

from __future__ import annotations

import os  # noqa: TC003
import subprocess
from pathlib import Path


class UnexpectedExecutableError(ValueError):
    """Raised when an executable does not match the allowed names."""

    def __init__(self, executable: str | os.PathLike[str]) -> None:
        super().__init__(f"unexpected executable: {executable}")


def ensure_allowed_executable(
    executable: str | os.PathLike[str], allowed_names: tuple[str, ...]
) -> str:
    """Return *executable* if its name matches one of *allowed_names*."""
    exec_path = Path(executable)
    candidate = exec_path.name.lower()
    allowed = {name.lower() for name in allowed_names}
    if candidate not in allowed:
        raise UnexpectedExecutableError(exec_path)
    return str(exec_path)


def run_validated(
    executable: str | os.PathLike[str],
    args: list[str] | tuple[str, ...],
    *,
    allowed_names: tuple[str, ...],
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    """Execute *executable* with *args* after validating its basename."""
    exec_path = ensure_allowed_executable(executable, allowed_names)
    return subprocess.run([exec_path, *args], **kwargs)  # noqa: S603
