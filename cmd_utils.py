"""Utilities for echoing and running external commands."""

from __future__ import annotations

import collections.abc as cabc
import shlex
import subprocess
import typing as t

import typer

Command = cabc.Sequence[str] | t.Any


def run_cmd(cmd: Command, *, fg: bool = False, **run_kwargs: t.Any) -> t.Any:  # noqa: ANN401
    """Echo ``cmd`` before running it."""
    if hasattr(cmd, "formulate"):
        typer.echo(f"$ {shlex.join(cmd.formulate())}")
        if fg:
            from plumbum import FG

            return cmd & FG
        if run_kwargs:
            return cmd.run(**run_kwargs)
        return cmd()
    typer.echo(f"$ {shlex.join(cmd)}")
    return subprocess.check_call(list(cmd))  # noqa: S603
