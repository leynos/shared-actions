"""Utility helpers for validating executables."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003
import os  # noqa: TC003
import typing as typ
from pathlib import Path

from plumbum import local

RunMethod = typ.Literal["call", "run", "run_fg"]

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import cmd_utils

    class _SupportsFormulate(typ.Protocol):
        def formulate(self) -> cabc.Sequence[str]: ...

    class _RunCmd(typ.Protocol):
        def __call__(
            self,
            cmd: _SupportsFormulate,
            *,
            method: RunMethod = "call",
            env: cabc.Mapping[str, str] | None = None,
            **run_kwargs: object,
        ) -> object: ...

    run_cmd: _RunCmd
else:
    from cmd_utils import coerce_run_result, run_cmd


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


@typ.overload
def run_validated(
    executable: str | os.PathLike[str],
    args: list[str] | tuple[str, ...],
    *,
    allowed_names: tuple[str, ...],
    method: typ.Literal["run"] = "run",
    env: cabc.Mapping[str, str] | None = None,
    **run_kwargs: object,
) -> cmd_utils.RunResult: ...


@typ.overload
def run_validated(
    executable: str | os.PathLike[str],
    args: list[str] | tuple[str, ...],
    *,
    allowed_names: tuple[str, ...],
    method: typ.Literal["call", "run_fg"],
    env: cabc.Mapping[str, str] | None = None,
    **run_kwargs: object,
) -> object: ...


def run_validated(
    executable: str | os.PathLike[str],
    args: list[str] | tuple[str, ...],
    *,
    allowed_names: tuple[str, ...],
    method: RunMethod = "run",
    env: cabc.Mapping[str, str] | None = None,
    **run_kwargs: object,
) -> object:
    """Execute *executable* with *args* after validating its basename."""
    exec_path = ensure_allowed_executable(executable, allowed_names)
    command = local[exec_path][list(args)] if args else local[exec_path]
    result = run_cmd(command, method=method, env=env, **run_kwargs)
    if method == "run":
        return coerce_run_result(typ.cast("cabc.Sequence[object]", result))
    return result
