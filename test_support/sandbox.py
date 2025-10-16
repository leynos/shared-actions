"""Sandbox test helpers for validate-linux-packages."""

from __future__ import annotations

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


class RaisingSandbox(DummySandbox):
    """Sandbox variant that raises ValidationError for specific commands."""

    def __init__(
        self,
        root: Path,
        calls: list[tuple[tuple[str, ...], int | None]],
        *,
        failure_command: tuple[str, ...],
        error: Exception,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(root, calls)
        self._failure_command = failure_command
        self._error = error
        self._cause = cause

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Raise the configured error when ``failure_command`` is executed."""
        if tuple(args) == self._failure_command:
            self._calls.append((tuple(args), timeout))
            if self._cause is not None:
                raise self._error from self._cause
            raise self._error
        return super().exec(*args, timeout=timeout)
