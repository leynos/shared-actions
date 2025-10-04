"""Utility helpers for validating executables."""

from __future__ import annotations

import os  # noqa: TC003
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc
    import subprocess

    class _RunCompletedProcess(typ.Protocol):
        def __call__(
            self,
            args: cabc.Sequence[str],
            *,
            capture_output: bool = False,
            check: bool = False,
            text: bool | None = None,
            encoding: str | None = None,
            errors: str | None = None,
            timeout: float | None = None,
            env: cabc.Mapping[str, str] | None = None,
            cwd: str | os.PathLike[str] | None = None,
            stdin: object | None = None,
            stdout: object | None = None,
            stderr: object | None = None,
            universal_newlines: bool | None = None,
        ) -> subprocess.CompletedProcess[str | bytes | None]: ...

    run_completed_process: _RunCompletedProcess
else:
    from cmd_utils import run_completed_process


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
    subprocess_kwargs: dict[str, object] = dict(kwargs)
    if (
        "text" not in subprocess_kwargs
        and "encoding" not in subprocess_kwargs
        and "universal_newlines" not in subprocess_kwargs
    ):
        subprocess_kwargs["text"] = True
    result = run_completed_process([exec_path, *args], **subprocess_kwargs)
    return typ.cast("subprocess.CompletedProcess[str]", result)
