"""Compatibility layer providing shellstub-like helpers via cmd_mox."""

from __future__ import annotations

import dataclasses as dc
import os
import sys
import types
import typing as typ
from pathlib import Path

from cmd_mox import CmdMox, Response
from cmd_mox.ipc import Invocation


@dc.dataclass(slots=True)
class Call:
    """Record of a command invocation captured by CmdMox."""

    argv: list[str]
    stdin: str
    stdout: str
    stderr: str
    exit_code: int
    env: dict[str, str]


class StubManager:
    """Adapter exposing a shellstub-like API backed by :mod:`cmd_mox`."""

    def __init__(self) -> None:
        self._controller = CmdMox()
        self._controller.__enter__()
        self._replayed = False
        self._closed = False
        self._env_cache: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        *,
        variants: typ.Sequence[typ.Mapping[str, typ.Any]] | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> None:
        """Register a stub for *name* using cmd_mox doubles."""

        stub = self._controller.stub(name)
        if variants:
            handler = self._build_variant_handler(
                variants,
                default_stdout=stdout,
                default_stderr=stderr,
                default_exit_code=exit_code,
            )
            stub.runs(handler)
        else:
            stub.returns(stdout=stdout, stderr=stderr, exit_code=exit_code)

    def calls_of(self, name: str) -> list[Call]:
        """Return all recorded invocations for *name* in replay order."""

        invocations = [
            inv for inv in list(self._controller.journal) if inv.command == name
        ]
        return [self._to_call(inv) for inv in invocations]

    @property
    def env(self) -> dict[str, str]:
        """Return environment variables exposing the shim directory."""

        self._ensure_replay()
        assert self._env_cache is not None
        return dict(self._env_cache)

    def close(self) -> None:
        """Verify outstanding interactions and restore the environment."""

        if self._closed:
            return
        try:
            if self._replayed:
                self._controller.verify()
        finally:
            exc_type, exc, tb = sys.exc_info()
            self._controller.__exit__(exc_type, exc, tb)
            self._closed = True

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> StubManager:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_replay(self) -> None:
        if self._replayed:
            return
        self._controller.replay()
        self._replayed = True
        env = dict(os.environ)
        try:
            import cmd_mox
        except ModuleNotFoundError:  # pragma: no cover - cmd_mox not installed
            self._env_cache = env
            return
        package_path = Path(cmd_mox.__file__).resolve().parent.parent
        existing = env.get("PYTHONPATH", "")
        if str(package_path) not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                f"{package_path}{os.pathsep}{existing}" if existing else str(package_path)
            )
        self._env_cache = env

    def _to_call(self, invocation: Invocation) -> Call:
        return Call(
            argv=list(invocation.args),
            stdin=invocation.stdin,
            stdout=invocation.stdout,
            stderr=invocation.stderr,
            exit_code=invocation.exit_code,
            env=dict(invocation.env),
        )

    def _build_variant_handler(
        self,
        variants: typ.Sequence[typ.Mapping[str, typ.Any]],
        *,
        default_stdout: str,
        default_stderr: str,
        default_exit_code: int,
    ) -> typ.Callable[[Invocation], Response]:
        prepared: list[tuple[list[str] | None, dict[str, typ.Any]]] = []
        default_spec: dict[str, typ.Any] | None = None
        for spec in variants:
            match = spec.get("match")
            match_list: list[str] | None
            if match is None:
                match_list = None
            else:
                match_list = list(match)
            response_spec = {
                "stdout": spec.get("stdout", ""),
                "stderr": spec.get("stderr", ""),
                "exit_code": spec.get("exit_code", 0),
            }
            if match_list is None and default_spec is None:
                default_spec = response_spec
            else:
                prepared.append((match_list, response_spec))
        if default_spec is None:
            default_spec = {
                "stdout": default_stdout,
                "stderr": default_stderr,
                "exit_code": default_exit_code,
            }

        def handler(invocation: Invocation) -> Response:
            for match_list, response_spec in prepared:
                if match_list is not None and list(invocation.args) == match_list:
                    return Response(**response_spec)
            return Response(**default_spec)

        return handler


__all__ = ["Call", "StubManager"]
