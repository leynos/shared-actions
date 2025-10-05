from __future__ import annotations

import collections.abc as cabc
import typing as typ
from subprocess import CalledProcessError, TimeoutExpired

from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

RunMethod = typ.Literal["call", "run", "run_fg"]

class RunResult(typ.NamedTuple):
    returncode: int
    stdout: str
    stderr: str

@typ.runtime_checkable
class SupportsFormulate(typ.Protocol):
    def formulate(self) -> cabc.Sequence[str]: ...

@typ.runtime_checkable
class SupportsCall(SupportsFormulate, typ.Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...

@typ.runtime_checkable
class SupportsRun(SupportsFormulate, typ.Protocol):
    def run(self, *args: object, **kwargs: object) -> object: ...

@typ.runtime_checkable
class SupportsRunFg(SupportsFormulate, typ.Protocol):
    def run_fg(self, **kwargs: object) -> object: ...

@typ.runtime_checkable
class SupportsAnd(SupportsFormulate, typ.Protocol):
    def __and__(self, other: object) -> object: ...

@typ.runtime_checkable
class SupportsWithEnv(SupportsFormulate, typ.Protocol):
    def with_env(self, **env: str) -> SupportsWithEnv: ...

def coerce_run_result(result: RunResult | cabc.Sequence[object]) -> RunResult: ...
def process_error_to_run_result(exc: ProcessExecutionError) -> RunResult: ...
def process_error_to_subprocess(
    exc: ProcessExecutionError | ProcessTimedOut,
    command: SupportsFormulate,
    *,
    timeout: float | None = ...,
) -> CalledProcessError | TimeoutExpired: ...
def run_cmd(
    cmd: object,
    *,
    method: RunMethod = ...,
    env: cabc.Mapping[str, str] | None = ...,
    **run_kwargs: object,
) -> object: ...

__all__ = [
    "RunMethod",
    "RunResult",
    "coerce_run_result",
    "process_error_to_run_result",
    "process_error_to_subprocess",
    "run_cmd",
]
