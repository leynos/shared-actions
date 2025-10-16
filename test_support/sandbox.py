"""Sandbox test helpers for validate-linux-packages."""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
else:  # pragma: no cover - runtime fallback
    Path = typ.Any


class DummySandbox:
    """Minimal sandbox session recording exec calls for assertions."""

    def __init__(
        self, root: Path, calls: list[tuple[tuple[str, ...], int | None]]
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._calls = calls
        self.isolation: str | None = None

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Record sandbox exec calls."""
        self._calls.append((tuple(args), timeout))
        return ""


@dataclasses.dataclass(frozen=True)
class SandboxFailure:
    """Describe a command that should fail inside a sandbox."""

    command: tuple[str, ...]
    error: Exception
    cause: BaseException | None = None


@dataclasses.dataclass
class SandboxContext:
    """Configuration for sandboxes used in tests."""

    root: Path
    calls: list[tuple[tuple[str, ...], int | None]]
    failure: SandboxFailure


class RaisingSandbox(DummySandbox):
    """Sandbox variant that raises ValidationError for specific commands."""

    def __init__(self, context: SandboxContext) -> None:
        super().__init__(context.root, context.calls)
        self._failure = context.failure

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Raise the configured error when ``failure_command`` is executed."""
        if tuple(args) == self._failure.command:
            self._calls.append((tuple(args), timeout))
            if self._failure.cause is not None:
                raise self._failure.error from self._failure.cause
            raise self._failure.error
        return super().exec(*args, timeout=timeout)
