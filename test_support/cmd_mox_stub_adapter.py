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
    ...     manager.register("git", default=DefaultResponse(stdout="v2.40.0\n"))
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


@dc.dataclass(slots=True)
class DefaultResponse:
    """Default response values for a stub command."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


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
        default: DefaultResponse | None = None,
    ) -> None:
        """Register a stub for *name* using cmd_mox doubles."""
        default = default or DefaultResponse()
        stub = self._controller.stub(name)
        if variants:
            handler = self._build_variant_handler(
                variants,
                default=default,
            )
            stub.runs(handler)
        else:
            stub.returns(
                stdout=default.stdout,
                stderr=default.stderr,
                exit_code=default.exit_code,
            )

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

    def _prepare_variants(
        self,
        variants: typ.Sequence[typ.Mapping[str, typ.Any]],
        default: DefaultResponse,
    ) -> tuple[list[tuple[list[str] | None, dict[str, typ.Any]]], dict[str, typ.Any]]:
        """Prepare variant specifications, returning (prepared_list, default_spec)."""
        prepared: list[tuple[list[str] | None, dict[str, typ.Any]]] = []
        default_spec: dict[str, typ.Any] | None = None
        fallback_spec = {
            "stdout": default.stdout,
            "stderr": default.stderr,
            "exit_code": default.exit_code,
        }
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
                continue
            prepared.append((match_list, response_spec))
        default_spec = default_spec or fallback_spec
        if default_spec is None:
            message = "Variant configuration must define a default response."
            raise ValueError(message)
        return prepared, default_spec

    def _match_invocation(
        self,
        invocation: Invocation,
        prepared: list[tuple[list[str] | None, dict[str, typ.Any]]],
    ) -> dict[str, typ.Any] | None:
        """Find the first prepared response matching the invocation's args."""
        args = list(invocation.args)
        return next(
            (
                response_spec
                for match_list, response_spec in prepared
                if match_list is not None and match_list == args
            ),
            None,
        )

    def _build_variant_handler(
        self,
        variants: typ.Sequence[typ.Mapping[str, typ.Any]],
        *,
        default: DefaultResponse,
    ) -> typ.Callable[[Invocation], Response]:
        # Collect (match pattern, response spec) pairs so replay can scan quickly.
        prepared, default_spec = self._prepare_variants(variants, default)

        def handler(invocation: Invocation) -> Response:
            # Return the first prepared response whose argv matches the invocation.
            matched = self._match_invocation(invocation, prepared)
            # Otherwise fall back to the default response determined above.
            return (
                Response(**matched) if matched is not None else Response(**default_spec)
            )

        return handler


__all__ = ["Call", "DefaultResponse", "StubManager"]
