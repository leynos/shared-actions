"""Utilities for echoing and running external commands.

Provides helpers that uniformly echo commands or execute them via
``plumbum`` while returning :class:`subprocess.CompletedProcess` objects.

Examples
--------
>>> from plumbum import local
>>> run_cmd(local["echo"]["hello"])
"""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003
import contextlib
import os
import shlex
import subprocess
import typing as typ

import typer
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

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


@typ.runtime_checkable
class _SupportsPlumbumRun(typ.Protocol):
    """Commands that can spawn subprocesses via :meth:`popen`."""

    def with_env(self, **env: str) -> _SupportsPlumbumRun: ...

    def popen(self, /, **kwargs: object) -> subprocess.Popen[typ.Any]: ...


KwargDict = dict[str, object]


def _prepare_stdio_kwargs(
    *,
    capture_output: bool,
    stdout: object | None,
    stderr: object | None,
) -> dict[str, object]:
    """Return keyword arguments that configure stdio handling."""
    if capture_output and (stdout is not None or stderr is not None):
        msg = "stdout and stderr arguments may not be used with capture_output"
        raise ValueError(msg)

    stdio_kwargs: dict[str, object] = {}
    if capture_output:
        stdio_kwargs["stdout"] = subprocess.PIPE
        stdio_kwargs["stderr"] = subprocess.PIPE
        return stdio_kwargs

    if stdout is not None:
        stdio_kwargs["stdout"] = stdout
    if stderr is not None:
        stdio_kwargs["stderr"] = stderr
    return stdio_kwargs


def _normalize_text_mode(
    *,
    text: bool | None,
    encoding: str | None,
    errors: str | None,
    universal_newlines: bool | None,
) -> bool | None:
    """Return the text mode flag derived from subprocess compatibility args."""
    text_mode = text if text is not None else universal_newlines
    if encoding is not None or errors is not None:
        return True
    return text_mode


def _collect_runtime_env(
    env: cabc.Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return an environment mapping reflecting local and process mutations."""
    plumbum_env = typ.cast("cabc.Mapping[str, str]", local.env)
    base_env: dict[str, str] = {key: str(value) for key, value in plumbum_env.items()}
    runtime_env = base_env.copy()
    runtime_env.update({key: str(value) for key, value in os.environ.items()})
    if env is not None:
        runtime_env.update({key: str(value) for key, value in env.items()})
    if runtime_env == base_env:
        return None
    return runtime_env


def _build_popen_kwargs(
    *,
    capture_output: bool,
    stdout: object | None,
    stderr: object | None,
    text_mode: bool | None,
    encoding: str | None,
    errors: str | None,
    cwd: str | os.PathLike[str] | None,
    stdin: object | None,
) -> dict[str, object]:
    """Return the keyword arguments passed to ``popen``."""
    popen_kwargs = _prepare_stdio_kwargs(
        capture_output=capture_output,
        stdout=stdout,
        stderr=stderr,
    )
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
    return popen_kwargs


def run_completed_process(
    cmd: object,
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
    """Execute *cmd* using :mod:`plumbum` and return a completed process."""
    if not isinstance(cmd, SupportsFormulate) or not isinstance(
        cmd, _SupportsPlumbumRun
    ):
        msg = "run_completed_process requires a plumbum command invocation"
        raise TypeError(msg)

    typer.echo(f"$ {cmd}")

    runtime_env = _collect_runtime_env(env)
    command_obj = typ.cast("SupportsFormulate | _SupportsPlumbumRun", cmd)
    if runtime_env:
        command_obj = typ.cast(
            "SupportsFormulate | _SupportsPlumbumRun",
            typ.cast("_SupportsPlumbumRun", command_obj).with_env(**runtime_env),
        )

    command_for_display = typ.cast("SupportsFormulate", command_obj)
    command_for_process = typ.cast("_SupportsPlumbumRun", command_obj)
    command_args = list(command_for_display.formulate())
    if not command_args:
        msg = "run_completed_process requires a plumbum command invocation"
        raise TypeError(msg)

    text_mode = _normalize_text_mode(
        text=text,
        encoding=encoding,
        errors=errors,
        universal_newlines=universal_newlines,
    )
    popen_kwargs = _build_popen_kwargs(
        capture_output=capture_output,
        stdout=stdout,
        stderr=stderr,
        text_mode=text_mode,
        encoding=encoding,
        errors=errors,
        cwd=cwd,
        stdin=stdin,
    )

    escaped_args = tuple(shlex.quote(part) for part in command_args)
    process = command_for_process.popen(**popen_kwargs)

    try:
        stdout_data, stderr_data = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        with contextlib.suppress(Exception):
            process.communicate()
        timeout_value = timeout if timeout is not None else exc.timeout
        raise subprocess.TimeoutExpired(
            escaped_args, timeout_value, output=exc.output, stderr=exc.stderr
        ) from exc

    captured_stdout: str | bytes | None = None
    captured_stderr: str | bytes | None = None
    if popen_kwargs.get("stdout") == subprocess.PIPE:
        captured_stdout = stdout_data
    if popen_kwargs.get("stderr") == subprocess.PIPE:
        captured_stderr = stderr_data

    completed: subprocess.CompletedProcess[str | bytes | None]
    completed = subprocess.CompletedProcess(
        escaped_args,
        process.returncode,
        stdout=captured_stdout,
        stderr=captured_stderr,
    )

    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            escaped_args,
            output=captured_stdout,
            stderr=captured_stderr,
        )

    return completed


def _merge_timeout(timeout: float | None, run_kwargs: KwargDict) -> float | None:
    """Return a merged timeout value."""
    if "timeout" in run_kwargs:
        if timeout is not None:
            raise TimeoutConflictError
        value = run_kwargs.pop("timeout")
        return typ.cast("float | None", value)
    return timeout


def _effective_timeout(
    timeout: float | None, exc: BaseException | None = None
) -> float:
    """Return the timeout value to report to ``TimeoutExpired``."""
    if timeout is not None:
        return timeout
    fallback = getattr(exc, "timeout", None)
    if isinstance(fallback, (int, float)):
        return float(fallback)
    return 0.0


def run_cmd(
    cmd: object,
    *,
    fg: bool = False,
    timeout: float | None = None,
    **run_kwargs: object,
) -> object:
    """Execute ``cmd`` while echoing it to stderr."""
    if not isinstance(cmd, SupportsFormulate):
        msg = "run_cmd requires a plumbum command invocation"
        raise TypeError(msg)

    command_args = [str(part) for part in cmd.formulate()]
    typer.echo(f"$ {cmd}")

    timeout = _merge_timeout(timeout, run_kwargs)

    if fg:
        capture_output = bool(run_kwargs.pop("capture_output", False))
        if capture_output:
            msg = "capture_output may not be used with fg=True"
            raise TypeError(msg)
        if timeout is not None:
            run_kwargs.setdefault("timeout", timeout)
        if isinstance(cmd, SupportsRunFg):
            try:
                if run_kwargs:
                    return cmd.run_fg(**run_kwargs)
                return cmd.run_fg()
            except ProcessTimedOut as exc:
                raise subprocess.TimeoutExpired(
                    command_args, _effective_timeout(timeout, exc)
                ) from exc
            except ProcessExecutionError as exc:
                raise subprocess.CalledProcessError(
                    typ.cast("int", exc.retcode),
                    command_args,
                    output=typ.cast("str | bytes | None", exc.stdout),
                    stderr=typ.cast("str | bytes | None", exc.stderr),
                ) from exc
        if isinstance(cmd, SupportsAnd) and not run_kwargs:
            from plumbum import FG  # pyright: ignore[reportMissingTypeStubs]

            return cmd & FG
        msg = "Command does not support foreground execution"
        raise TypeError(msg)

    if timeout is not None:
        run_kwargs.setdefault("timeout", timeout)

    if run_kwargs:
        if not isinstance(cmd, SupportsRun):
            invalid_keys = sorted(run_kwargs.keys())
            msg = f"Command does not accept keyword arguments: {invalid_keys}"
            raise TypeError(msg)
        try:
            return cmd.run(**run_kwargs)
        except ProcessTimedOut as exc:
            raise subprocess.TimeoutExpired(
                command_args, _effective_timeout(timeout, exc)
            ) from exc
        except ProcessExecutionError as exc:
            raise subprocess.CalledProcessError(
                typ.cast("int", exc.retcode),
                command_args,
                output=typ.cast("str | bytes | None", exc.stdout),
                stderr=typ.cast("str | bytes | None", exc.stderr),
            ) from exc
    try:
        return cmd()
    except ProcessTimedOut as exc:
        raise subprocess.TimeoutExpired(
            command_args, _effective_timeout(timeout, exc)
        ) from exc
    except ProcessExecutionError as exc:
        raise subprocess.CalledProcessError(
            typ.cast("int", exc.retcode),
            command_args,
            output=typ.cast("str | bytes | None", exc.stdout),
            stderr=typ.cast("str | bytes | None", exc.stderr),
        ) from exc
