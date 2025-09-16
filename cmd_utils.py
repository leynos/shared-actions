"""Utilities for echoing and running external commands."""

from __future__ import annotations

import collections.abc as cabc
import shlex
import subprocess
import typing as t

import typer


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


def run_cmd(cmd: Command, *, fg: bool = False, **run_kwargs: t.Any) -> t.Any:  # noqa: ANN401
    """Echo ``cmd`` before running it."""
    if isinstance(cmd, cabc.Sequence):
        typer.echo(f"$ {shlex.join(cmd)}")
        timeout = run_kwargs.pop("timeout", None) if fg else None
        if run_kwargs:
            msg = "Sequence commands do not accept keyword arguments"
            raise TypeError(msg)
        if fg:
            subprocess.run(list(cmd), check=True, timeout=timeout)  # noqa: S603
            return 0
        return subprocess.check_call(list(cmd))  # noqa: S603
    args = cmd.formulate()
    typer.echo(f"$ {shlex.join(args)}")
    if fg:
        timeout = run_kwargs.pop("timeout", None)
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
            subprocess.run(list(args), check=True, timeout=timeout)  # noqa: S603
            return 0
        if run_kwargs and isinstance(cmd, SupportsRunFg):
            cmd.run_fg(**run_kwargs)
            return 0
        if isinstance(cmd, SupportsAnd):
            from plumbum import FG  # pyright: ignore[reportMissingTypeStubs]

            return cmd & FG
        if run_kwargs:
            msg = "Command does not support foreground execution with keyword arguments"
            raise TypeError(msg)
        return cmd()
    if run_kwargs:
        if isinstance(cmd, SupportsRun):
            return cmd.run(**run_kwargs)
        msg = "Command does not accept keyword arguments"
        raise TypeError(msg)
    return cmd()
