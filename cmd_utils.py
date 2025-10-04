"""Utilities for echoing and running external commands.

Provides helpers that uniformly echo commands or execute them via
``plumbum`` while returning :class:`subprocess.CompletedProcess` objects.

Examples
--------
>>> from plumbum import local
>>> run_cmd(["echo", "hello"])
>>> run_cmd(local["echo"]["hello"])
"""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import os
import shlex
import subprocess
import typing as typ

import typer
from plumbum import local
from plumbum.commands.processes import (  # pyright: ignore[reportMissingTypeStubs]
    ProcessTimedOut,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing only

    class _SupportsPlumbumRun(typ.Protocol):
        def with_env(self, **env: str) -> _SupportsPlumbumRun: ...

        def run(
            self, /, *args: object, **kwargs: object
        ) -> tuple[int, str | None, str | None]: ...

        def popen(self, /, **kwargs: object) -> subprocess.Popen[typ.Any]: ...


__all__: list[str] = [
    "run_cmd",
    "run_completed_process",
]


class TimeoutConflictError(TypeError):
    """Raised when mutually exclusive timeout options are provided."""

    def __init__(self) -> None:
        super().__init__("timeout specified via parameter and run_kwargs")


@typ.runtime_checkable
class SupportsFormulate(typ.Protocol):
    """Objects that expose a shell representation via ``formulate``."""

    def formulate(self) -> cabc.Sequence[str]:  # pragma: no cover - protocol
        ...

    def __call__(
        self, *args: object, **run_kwargs: object
    ) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsRun(typ.Protocol):
    """Commands that support ``run`` with keyword arguments."""

    def run(
        self, *args: object, **run_kwargs: object
    ) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsRunFg(typ.Protocol):
    """Commands that expose ``run_fg`` for foreground execution."""

    def run_fg(self, **run_kwargs: object) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsAnd(typ.Protocol):
    """Commands that implement ``cmd & FG`` semantics."""

    def __and__(self, other: object) -> object:  # pragma: no cover - protocol
        ...


Command = cabc.Sequence[str] | SupportsFormulate

KwargDict = dict[str, object]


def run_completed_process(
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
) -> subprocess.CompletedProcess[str | bytes | None]:
    """Execute *args* using :mod:`plumbum` and return a completed process."""
    command_args = [str(part) for part in args]
    if not command_args:
        msg = "run_completed_process requires at least one argument"
        raise ValueError(msg)

    # Sync plumbum's environment with any runtime mutations (e.g. cmd-mox shims).
    env_mapping = typ.cast(
        "cabc.MutableMapping[str, str]",
        local.env,
    )
    env_mapping.update(dict(os.environ))

    if capture_output and (stdout is not None or stderr is not None):
        msg = "stdout and stderr arguments may not be used with capture_output"
        raise ValueError(msg)

    command_obj = typ.cast(
        "_SupportsPlumbumRun", local[command_args[0]][command_args[1:]]
    )
    if env is not None:
        command_obj = command_obj.with_env(
            **{key: str(value) for key, value in env.items()}
        )

    text_mode: bool | None = text if text is not None else universal_newlines
    if encoding is not None or errors is not None:
        text_mode = True

    popen_kwargs: dict[str, object] = {}
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    else:
        if stdout is not None:
            popen_kwargs["stdout"] = stdout
        if stderr is not None:
            popen_kwargs["stderr"] = stderr

    if text_mode is not None:
        popen_kwargs["text"] = text_mode
    if encoding is not None:
        popen_kwargs["encoding"] = encoding
    if errors is not None:
        popen_kwargs["errors"] = errors
    if cwd is not None:
        popen_kwargs["cwd"] = os.fspath(cwd)
    if stdin is not None:
        popen_kwargs["stdin"] = stdin
    process = command_obj.popen(**popen_kwargs)

    try:
        stdout_data, stderr_data = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        with contextlib.suppress(Exception):
            process.communicate()
        timeout_value = timeout if timeout is not None else exc.timeout
        raise subprocess.TimeoutExpired(
            command_args, timeout_value, output=exc.output, stderr=exc.stderr
        ) from exc

    returncode = process.returncode

    captured_stdout: str | bytes | None = None
    captured_stderr: str | bytes | None = None
    if popen_kwargs.get("stdout") == subprocess.PIPE:
        captured_stdout = stdout_data
    if popen_kwargs.get("stderr") == subprocess.PIPE:
        captured_stderr = stderr_data

    completed: subprocess.CompletedProcess[str | bytes | None]
    completed = subprocess.CompletedProcess(
        command_args,
        returncode,
        stdout=captured_stdout,
        stderr=captured_stderr,
    )

    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode,
            command_args,
            output=captured_stdout,
            stderr=captured_stderr,
        )

    return completed


def _merge_timeout(timeout: float | None, run_kwargs: KwargDict) -> float | None:
    """Return a merged timeout value.

    Parameters
    ----------
    timeout : float or None
        Timeout supplied via the dedicated parameter.
    run_kwargs : dict of str to Any
        Keyword arguments passed to ``run_cmd``.

    Returns
    -------
    float or None
        The timeout to enforce, favouring ``run_kwargs`` when provided.
    """
    if "timeout" in run_kwargs:
        if timeout is not None:
            raise TimeoutConflictError
        value = run_kwargs.pop("timeout")
        return typ.cast("float | None", value)
    return timeout


def run_cmd(
    cmd: Command,
    *,
    fg: bool = False,
    timeout: float | None = None,
    **run_kwargs: object,
) -> object:
    """Execute ``cmd`` while echoing it to stderr.

    Parameters
    ----------
    cmd : Sequence of str or SupportsFormulate
        Shell command to execute.
    fg : bool, optional
        When ``True``, stream subprocess output to the console.
    timeout : float or None, optional
        Kill the process after this many seconds when supported.
    **run_kwargs : Any
        Adapter-specific keyword arguments passed through to ``run``/``run_fg``.

    Returns
    -------
    Any
        Result returned by the underlying command adapter.
    """
    timeout = _merge_timeout(timeout, run_kwargs)
    if isinstance(cmd, cabc.Sequence):
        typer.echo(f"$ {shlex.join(cmd)}")
        if run_kwargs:
            msg = (
                "Sequence commands do not accept keyword arguments: "
                f"{sorted(run_kwargs.keys())}"
            )
            raise TypeError(msg)
        if fg:
            subprocess.run(list(cmd), check=True, timeout=timeout)  # noqa: S603, TID251
            return 0
        return subprocess.check_call(list(cmd), timeout=timeout)  # noqa: S603, TID251

    args = list(cmd.formulate())
    typer.echo(f"$ {shlex.join(args)}")
    if fg:
        if timeout is not None:
            if isinstance(cmd, SupportsRun):
                run_kwargs.setdefault("stdout", None)
                run_kwargs.setdefault("stderr", None)
                try:
                    cmd.run(timeout=timeout, **run_kwargs)
                except ProcessTimedOut as exc:
                    raise subprocess.TimeoutExpired(args, timeout) from exc
                return 0
            subprocess.run(args, check=True, timeout=timeout)  # noqa: S603, TID251
            return 0
        if isinstance(cmd, SupportsRunFg):
            if run_kwargs:
                cmd.run_fg(**run_kwargs)
            else:
                cmd.run_fg()
            return 0
        if isinstance(cmd, SupportsAnd) and not run_kwargs:
            from plumbum import FG  # pyright: ignore[reportMissingTypeStubs]

            return cmd & FG
        if run_kwargs:
            msg = (
                "Command does not support foreground execution with keyword arguments: "
                f"{sorted(run_kwargs.keys())}"
            )
            raise TypeError(msg)
        result = cmd()
        return result if isinstance(result, int) else 0

    if not fg and timeout is not None and isinstance(cmd, SupportsRun):
        run_kwargs.setdefault("timeout", timeout)

    if run_kwargs:
        if isinstance(cmd, SupportsRun):
            return cmd.run(**run_kwargs)
        msg = f"Command does not accept keyword arguments: {sorted(run_kwargs.keys())}"
        raise TypeError(msg)
    return cmd()
