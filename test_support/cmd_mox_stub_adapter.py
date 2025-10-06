r"""Compatibility layer providing shellstub-like helpers via cmd_mox.

This module exposes :class:`StubManager`, a thin adapter over
``cmd_mox.CmdMox`` that preserves the historical ``shellstub`` API. It allows
tests that previously depended on ``shellstub`` to migrate to ``cmd_mox`` while
leaving their assertions and fixtures untouched.

Usage
-----
Create a :class:`~cmd_mox.CmdMox` controller, pass it to
:class:`StubManager`, register expected command invocations, then replay and
inspect the captured calls:

    >>> from cmd_mox import CmdMox
    >>> with CmdMox() as controller:
    ...     manager = StubManager(controller)
    ...     manager.register("git", stdout="v2.40.0\n")
    ...     env = manager.env
    ...     # Execute code under test using ``env``
    ...     calls = manager.calls_of("git")
    ...     manager.close()

The manager can be reused across tests by injecting a shared controller (for
example through a pytest fixture) and calling :meth:`close` between test cases.
"""

from __future__ import annotations

import dataclasses as dc
import os
import typing as typ
from pathlib import Path

from cmd_mox.ipc import Invocation, Response

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


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

    def __init__(self, controller: CmdMox) -> None:
        self._controller = controller
        self._env_cache: dict[str, str] | None = None
        self._replayed = False

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
        self._ensure_replay()
        return [
            self._to_call(invocation)
            for invocation in self._controller.journal
            if invocation.command == name
        ]

    @property
    def env(self) -> dict[str, str]:
        """Return environment variables exposing the shim directory."""
        self._ensure_replay()
        assert self._env_cache is not None
        return dict(self._env_cache)

    def close(self) -> None:
        """Reset cached environment for idempotent fixture teardown."""
        self._env_cache = None
        self._replayed = False

    def __enter__(self) -> StubManager:
        """Enable ``with StubManager(...)`` usage."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: typ.TracebackType | None,
    ) -> None:
        """Ensure cached state is cleared when leaving a context."""
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_replay(self) -> None:
        if self._replayed:
            return
        self._controller.replay()
        env = dict(os.environ)
        try:
            import cmd_mox
        except ModuleNotFoundError:  # pragma: no cover - defensive guard
            self._env_cache = env
        else:
            package_path = Path(cmd_mox.__file__).resolve().parent.parent
            existing = env.get("PYTHONPATH", "")
            entries = [part for part in existing.split(os.pathsep) if part]
            if str(package_path) not in entries:
                updated = [str(package_path), *entries]
                env["PYTHONPATH"] = os.pathsep.join(updated)
            self._env_cache = env
        self._replayed = True

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
        # Collect (match pattern, response spec) pairs so replay can scan quickly.
        for spec in variants:
            # Normalize the declared match pattern and response payload.
            match = spec.get("match")
            match_list: list[str] | None = None if match is None else list(match)
            # Store stdout/stderr/exit_code defaults for the variant.
            response_spec = {
                "stdout": spec.get("stdout", ""),
                "stderr": spec.get("stderr", ""),
                "exit_code": spec.get("exit_code", 0),
            }
            # Treat the first variant without an explicit match as the default response.
            if match_list is None and default_spec is None:
                default_spec = response_spec
            else:
                prepared.append((match_list, response_spec))
        if default_spec is None:
            # Use register() defaults when variants omit a baseline response.
            default_spec = {
                "stdout": default_stdout,
                "stderr": default_stderr,
                "exit_code": default_exit_code,
            }

        def handler(invocation: Invocation) -> Response:
            # Return the first prepared response whose argv matches the invocation.
            for match_list, response_spec in prepared:
                if match_list is not None and list(invocation.args) == match_list:
                    return Response(**response_spec)
            # Otherwise fall back to the default response determined above.
            return Response(**default_spec)

        return handler


__all__ = ["Call", "StubManager"]
