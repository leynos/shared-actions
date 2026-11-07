"""Type stubs for the public API of :mod:`cmd_utils`."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from subprocess import CalledProcessError, TimeoutExpired

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

    def formulate(self) -> cabc.Sequence[str]:
        """Return the command as its sequence of argument strings."""
        ...

@typ.runtime_checkable
class SupportsCall(SupportsFormulate, typ.Protocol):
    """Commands that can be invoked like ``cmd()``."""

    def __call__(self, *args: object, **kwargs: object) -> object:
        """Invoke the command with the given arguments."""
        ...

@typ.runtime_checkable
class SupportsRun(SupportsFormulate, typ.Protocol):
    """Commands that implement :meth:`run`."""

    def run(self, *args: object, **kwargs: object) -> object:
        """Execute the command through plumbum's ``run`` method."""
        ...

@typ.runtime_checkable
class SupportsRunFg(SupportsFormulate, typ.Protocol):
    """Commands that expose :meth:`run_fg` for foreground execution."""

    def run_fg(self, **kwargs: object) -> object:
        """Execute the command in the foreground through ``run_fg``."""
        ...

@typ.runtime_checkable
class SupportsAnd(SupportsFormulate, typ.Protocol):
    """Commands that can be combined with ``FG`` using ``&``."""

    def __and__(self, other: object) -> object:
        """Combine the command with ``other`` using ``&``."""
        ...

@typ.runtime_checkable
class SupportsWithEnv(SupportsFormulate, typ.Protocol):
    """Commands that support environment overrides via :meth:`with_env`."""

    def with_env(self, **env: str) -> SupportsWithEnv:
        """Return the command with ``env`` overrides applied."""
        ...

def coerce_run_result(result: RunResult | cabc.Sequence[object]) -> RunResult:
    """Normalize *result* into a :class:`RunResult`."""
    ...

def process_error_to_run_result(exc: ProcessExecutionError) -> RunResult:
    """Convert ``exc`` into a :class:`RunResult` for consistent handling."""
    ...

def process_error_to_subprocess(
    exc: ProcessExecutionError | ProcessTimedOut,
    command: SupportsFormulate,
    *,
    timeout: float | None = ...,
) -> CalledProcessError | TimeoutExpired:
    """Map plumbum exceptions to their :mod:`subprocess` counterparts."""
    ...

def run_cmd(
    cmd: object,
    *,
    method: RunMethod = ...,
    env: cabc.Mapping[str, str] | None = ...,
    **run_kwargs: object,
) -> object:
    """Execute ``cmd`` using plumbum semantics after echoing it."""
    ...

__all__ = [
    "RunMethod",
    "RunResult",
    "coerce_run_result",
    "process_error_to_run_result",
    "process_error_to_subprocess",
    "run_cmd",
]
