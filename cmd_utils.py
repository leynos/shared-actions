r"""Utilities for running plumbum command invocations.

This module provides :func:`run_cmd`, a unified interface for executing
plumbum commands with optional environment overrides and multiple execution
strategies (``call`` by default, plus ``run`` and ``run_fg``). Each invocation
is echoed before execution to aid debugging in CI logs or local terminals.

Examples
--------
Basic usage with the default ``call`` strategy::

    >>> from plumbum import local
    >>> result = run_cmd(local["echo"]["hello"])
    $ echo hello
    'hello\n'

Overriding the environment for a single command::

    >>> cmd = local["env"]["MY_VAR"]
    >>> run_cmd(cmd, env={"MY_VAR": "custom_value"})
    $ env MY_VAR
    'custom_value\n'

Streaming output in the foreground via ``run_fg``::

    >>> run_cmd(local["make"]["test"], method="run_fg")
    $ make test
    # Output streams directly to stdout/stderr

Inspecting exit status and stderr with the ``run`` method::

    >>> failure = run_cmd(
    ...     local["python"]["-c", "import sys; sys.stderr.write('oops'); sys.exit(1)"],
    ...     method="run",
    ... )
    $ python -c "import sys; sys.stderr.write('oops'); sys.exit(1)"
    >>> failure.returncode
    1
    >>> failure.stderr
    'oops'

Enforcing a timeout for long-running processes::

    >>> run_cmd(
    ...     local["python"]["-c", "import time; time.sleep(5)"],
    ...     method="run",
    ...     timeout=0.01,
    ... )
    Traceback (most recent call last):
    ProcessTimedOut: ...
"""

from __future__ import annotations

import ast
import collections.abc as cabc
import os
import subprocess
import typing as typ

import typer
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

RunMethod = typ.Literal["call", "run", "run_fg"]


class RunResult(typ.NamedTuple):
    """Structured representation of plumbum ``run`` results."""

    returncode: int
    stdout: str
    stderr: str


@typ.runtime_checkable
class SupportsFormulate(typ.Protocol):
    """Objects that expose a shell representation via ``formulate``."""

    def formulate(self) -> cabc.Sequence[str]:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsCall(SupportsFormulate, typ.Protocol):
    """Commands that can be invoked like ``cmd()``."""

    def __call__(
        self, *args: object, **kwargs: object
    ) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsRun(SupportsFormulate, typ.Protocol):
    """Commands that implement :meth:`run`."""

    def run(
        self, *args: object, **run_kwargs: object
    ) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsRunFg(SupportsFormulate, typ.Protocol):
    """Commands that expose :meth:`run_fg` for foreground execution."""

    def run_fg(self, **run_kwargs: object) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsAnd(SupportsFormulate, typ.Protocol):
    """Commands that can be combined with ``FG`` using ``&``."""

    def __and__(self, other: object) -> object:  # pragma: no cover - protocol
        ...


@typ.runtime_checkable
class SupportsWithEnv(SupportsFormulate, typ.Protocol):
    """Commands that support environment overrides via :meth:`with_env`."""

    def with_env(self, **env: str) -> SupportsWithEnv:  # pragma: no cover - protocol
        ...


def _ensure_text(value: str | bytes | None) -> str:
    """Return ``value`` as a decoded ``str`` replacing undecodable bytes."""
    if isinstance(value, str):
        if value.startswith(("b'", 'b"', "bytearray(", "bytes(")):
            try:
                literal = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return value
            if isinstance(literal, (bytes, bytearray)):
                return bytes(literal).decode("utf-8", errors="replace")
        return value
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace")


def coerce_run_result(
    result: RunResult | cabc.Sequence[object],
) -> RunResult:
    """Normalise *result* into a :class:`RunResult`."""
    if isinstance(result, RunResult):
        return result
    try:
        returncode_obj, stdout_obj, stderr_obj = result  # type: ignore[misc]
    except ValueError as exc:  # pragma: no cover - defensive programming
        msg = "plumbum run() results must unpack into (returncode, stdout, stderr)"
        raise TypeError(msg) from exc
    return RunResult(
        int(typ.cast("int", returncode_obj)),
        _ensure_text(typ.cast("str | bytes | None", stdout_obj)),
        _ensure_text(typ.cast("str | bytes | None", stderr_obj)),
    )


def process_error_to_run_result(exc: ProcessExecutionError) -> RunResult:
    """Convert ``exc`` into a :class:`RunResult` for consistent handling."""
    return RunResult(
        int(exc.retcode),
        _ensure_text(getattr(exc, "stdout", "")),
        _ensure_text(getattr(exc, "stderr", "")),
    )


def process_error_to_subprocess(
    exc: ProcessExecutionError | ProcessTimedOut,
    command: SupportsFormulate,
    *,
    timeout: float | None = None,
) -> subprocess.CalledProcessError | subprocess.TimeoutExpired:
    """Map plumbum exceptions to their :mod:`subprocess` counterparts."""
    formatted = [str(part) for part in command.formulate()]
    if isinstance(exc, ProcessExecutionError):
        return subprocess.CalledProcessError(
            int(exc.retcode),
            formatted,
            output=_ensure_text(getattr(exc, "stdout", "")),
            stderr=_ensure_text(getattr(exc, "stderr", "")),
        )
    raw_timeout = getattr(exc, "timeout", None)
    fallback_timeout = timeout if isinstance(timeout, (int, float)) else None
    if isinstance(raw_timeout, (int, float)):
        timeout_value = float(raw_timeout)
    elif fallback_timeout is not None:
        timeout_value = float(fallback_timeout)
    else:
        timeout_value = 0.0
    return subprocess.TimeoutExpired(
        cmd=formatted,
        timeout=timeout_value,
        output=_ensure_text(getattr(exc, "stdout", "")),
        stderr=_ensure_text(getattr(exc, "stderr", "")),
    )


def _collect_runtime_env(
    env: cabc.Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return an environment mapping reflecting local and process mutations."""
    plumbum_env = typ.cast("cabc.Mapping[str, str]", local.env)
    base_env = {key: str(value) for key, value in plumbum_env.items()}

    if env is not None:
        return {key: str(value) for key, value in env.items()}

    runtime_env = base_env | {key: str(value) for key, value in os.environ.items()}
    return None if runtime_env == base_env else runtime_env


def _apply_environment(
    cmd: SupportsFormulate,
    runtime_env: dict[str, str] | None,
) -> SupportsFormulate:
    """Return *cmd* with *runtime_env* applied when provided."""
    if runtime_env is None:
        return cmd
    if not isinstance(cmd, SupportsWithEnv):  # pragma: no cover - defensive
        msg = "Command does not support environment overrides"
        raise TypeError(msg)
    return typ.cast("SupportsFormulate", cmd.with_env(**runtime_env))


def run_cmd(
    cmd: object,
    *,
    method: RunMethod = "call",
    env: cabc.Mapping[str, str] | None = None,
    **run_kwargs: object,
) -> object:
    """Execute ``cmd`` using plumbum semantics after echoing it."""
    if not isinstance(cmd, SupportsFormulate):
        msg = "run_cmd requires a plumbum command invocation"
        raise TypeError(msg)

    typer.echo(f"$ {cmd}")

    prepared = _apply_environment(cmd, _collect_runtime_env(env))
    handler = _RUN_HANDLERS.get(method)
    if handler is None:
        msg = f"Unknown run method: {method}"
        raise ValueError(msg)
    return handler(prepared, run_kwargs)


def _call_handler(command: SupportsFormulate, run_kwargs: dict[str, object]) -> object:
    if not isinstance(command, SupportsCall):
        msg = "Command does not support call semantics"
        raise TypeError(msg)
    return command(**run_kwargs)


def _run_handler(
    command: SupportsFormulate, run_kwargs: dict[str, object]
) -> RunResult:
    if not isinstance(command, SupportsRun):
        msg = "Command does not support run()"
        raise TypeError(msg)
    run_options = dict(run_kwargs)
    run_options.setdefault("retcode", None)
    try:
        raw_result = command.run(**run_options)
    except ProcessTimedOut:
        raise
    except TimeoutError as exc:
        timeout_value = run_options.get("timeout", getattr(exc, "timeout", None))
        if isinstance(timeout_value, (int, float)):
            normalized_timeout: float | None = float(timeout_value)
        else:
            normalized_timeout = None
        stdout = _ensure_text(getattr(exc, "stdout", ""))
        stderr = _ensure_text(getattr(exc, "stderr", ""))
        formatted = [str(part) for part in command.formulate()]
        timeout_message = str(exc) or "Command timed out"
        resolved_timeout = normalized_timeout if normalized_timeout is not None else 0.0
        timed_out = ProcessTimedOut(  # type: ignore[unknown-argument]
            formatted,
            resolved_timeout,
            stdout=stdout,  # type: ignore[unknown-argument]
            stderr=stderr,  # type: ignore[unknown-argument]
        )
        timed_out.args = (timeout_message, *timed_out.args[1:])
        raise timed_out from exc
    return coerce_run_result(typ.cast("cabc.Sequence[object]", raw_result))


def _run_fg_handler(
    command: SupportsFormulate, run_kwargs: dict[str, object]
) -> object:
    if isinstance(command, SupportsRunFg):
        return command.run_fg(**run_kwargs)
    if run_kwargs:
        invalid = ", ".join(sorted(run_kwargs.keys()))
        msg = f"Foreground execution does not accept keyword arguments: {invalid}"
        raise TypeError(msg)
    if isinstance(command, SupportsAnd):
        from plumbum import FG  # pyright: ignore[reportMissingTypeStubs]

        return command & FG
    msg = "Command does not support foreground execution"
    raise TypeError(msg)


_MethodHandler = cabc.Callable[[SupportsFormulate, dict[str, object]], object]

_RUN_HANDLERS: dict[RunMethod, _MethodHandler] = {
    "call": _call_handler,
    "run": typ.cast("_MethodHandler", _run_handler),
    "run_fg": _run_fg_handler,
}


__all__ = [
    "RunMethod",
    "RunResult",
    "coerce_run_result",
    "process_error_to_run_result",
    "process_error_to_subprocess",
    "run_cmd",
]
