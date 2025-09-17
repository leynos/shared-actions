"""Utilities for echoing and running external commands.

Provides a single entrypoint, ``run_cmd``, that uniformly echoes and
executes either raw argv sequences or adapter objects exposing
``formulate()``.

Examples
--------
>>> from plumbum import local
>>> run_cmd(["echo", "hello"])
>>> run_cmd(local["echo"]["hello"])
"""

from __future__ import annotations

import collections.abc as cabc
import shlex
import subprocess
import typing as t

import typer

__all__ = [
    "run_cmd",
]


@t.runtime_checkable
class SupportsFormulate(t.Protocol):
    """Objects that expose a shell representation via ``formulate``."""

    def formulate(self) -> cabc.Sequence[str]:  # pragma: no cover - protocol
        ...

    def __call__(
        self, *args: t.Any, **run_kwargs: t.Any
    ) -> t.Any:  # pragma: no cover - protocol
        ...


@t.runtime_checkable
class SupportsRun(t.Protocol):
    """Commands that support ``run`` with keyword arguments."""

    def run(
        self, *args: t.Any, **run_kwargs: t.Any
    ) -> t.Any:  # pragma: no cover - protocol
        ...


@t.runtime_checkable
class SupportsRunFg(t.Protocol):
    """Commands that expose ``run_fg`` for foreground execution."""

    def run_fg(self, **run_kwargs: t.Any) -> t.Any:  # pragma: no cover - protocol
        ...


@t.runtime_checkable
class SupportsAnd(t.Protocol):
    """Commands that implement ``cmd & FG`` semantics."""

    def __and__(self, other: t.Any) -> t.Any:  # pragma: no cover - protocol
        ...


Command = cabc.Sequence[str] | SupportsFormulate


def _merge_timeout(timeout: float | None, run_kwargs: dict[str, t.Any]) -> float | None:
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
            raise TypeError("timeout specified via parameter and run_kwargs")
        value = run_kwargs.pop("timeout")
        return t.cast("float | None", value)
    return timeout


def run_cmd(
    cmd: Command,
    *,
    fg: bool = False,
    timeout: float | None = None,
    **run_kwargs: t.Any,
) -> t.Any:  # noqa: ANN401  # NOTE: heterogeneous kwargs required for plumbum/subprocess compatibility
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
            subprocess.run(list(cmd), check=True, timeout=timeout)  # noqa: S603
            return 0
        return subprocess.check_call(list(cmd), timeout=timeout)  # noqa: S603

    args = list(cmd.formulate())
    typer.echo(f"$ {shlex.join(args)}")
    if fg:
        if timeout is not None:
            if isinstance(cmd, SupportsRun):
                run_kwargs.setdefault("stdout", None)
                run_kwargs.setdefault("stderr", None)
                from plumbum.commands.processes import (  # pyright: ignore[reportMissingTypeStubs]
                    ProcessTimedOut,
                )

                try:
                    cmd.run(timeout=timeout, **run_kwargs)
                except ProcessTimedOut as exc:
                    raise subprocess.TimeoutExpired(args, timeout) from exc
                return 0
            subprocess.run(args, check=True, timeout=timeout)  # noqa: S603
            return 0
        if run_kwargs and isinstance(cmd, SupportsRunFg):
            cmd.run_fg(**run_kwargs)
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
        return cmd()

    if not fg and timeout is not None and isinstance(cmd, SupportsRun):
        run_kwargs.setdefault("timeout", timeout)

    if run_kwargs:
        if isinstance(cmd, SupportsRun):
            return cmd.run(**run_kwargs)
        msg = f"Command does not accept keyword arguments: {sorted(run_kwargs.keys())}"
        raise TypeError(msg)
    return cmd()
