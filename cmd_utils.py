"""Utilities for running plumbum command invocations."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003
import os
import typing as typ

import typer
from plumbum import local

__all__ = ["RunMethod", "run_cmd"]


RunMethod = typ.Literal["call", "run", "run_fg"]


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


def _collect_runtime_env(
    env: cabc.Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return an environment mapping reflecting local and process mutations."""
    plumbum_env = typ.cast("cabc.Mapping[str, str]", local.env)
    base_env = {key: str(value) for key, value in plumbum_env.items()}

    if env is not None:
        return {key: str(value) for key, value in env.items()}

    runtime_env = base_env.copy()
    runtime_env.update({key: str(value) for key, value in os.environ.items()})
    if runtime_env == base_env:
        return None
    return runtime_env


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

    if method == "call":
        if not isinstance(prepared, SupportsCall):
            msg = "Command does not support call semantics"
            raise TypeError(msg)
        return prepared(**run_kwargs)

    if method == "run":
        if not isinstance(prepared, SupportsRun):
            msg = "Command does not support run()"
            raise TypeError(msg)
        run_options = dict(run_kwargs)
        run_options.setdefault("retcode", None)
        return prepared.run(**run_options)

    if method == "run_fg":
        if isinstance(prepared, SupportsRunFg):
            return prepared.run_fg(**run_kwargs)
        if run_kwargs:
            invalid = ", ".join(sorted(run_kwargs.keys()))
            msg = f"Foreground execution does not accept keyword arguments: {invalid}"
            raise TypeError(msg)
        if isinstance(prepared, SupportsAnd):
            from plumbum import FG  # pyright: ignore[reportMissingTypeStubs]

            return prepared & FG
        msg = "Command does not support foreground execution"
        raise TypeError(msg)

    msg = f"Unknown run method: {method}"
    raise ValueError(msg)
