"""Helpers for interacting with the polythene sandbox CLI."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import typing as typ

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from validate_commands import run_text
from validate_exceptions import ValidationError
from validate_helpers import ensure_directory

if typ.TYPE_CHECKING:  # pragma: no cover - import used for typing only
    from pathlib import Path
else:  # pragma: no cover - runtime fallback for type checking
    Path = typ.Any

logger = logging.getLogger(__name__)

Command = tuple[str, ...]
DEFAULT_POLYTHENE_COMMAND: Command = ("polythene",)
DEFAULT_ISOLATION = "proot"

__all__ = sorted(
    (
        "Command",
        "DEFAULT_ISOLATION",
        "DEFAULT_POLYTHENE_COMMAND",
        "PolytheneSession",
        "default_polythene_command",
        "polythene_rootfs",
    )
)


_ISOLATION_ERROR_PATTERNS = (
    r"All isolation modes unavailable",
    r"Required command not found",
    r"setting up uid map.*permission denied",
)
_STDERR_SNIPPET_LIMIT = 400


def _decode_stream(value: object) -> str:
    """Return ``value`` normalised as a UTF-8 ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _stderr_snippet(
    stderr_text: str, *, limit: int = _STDERR_SNIPPET_LIMIT
) -> str | None:
    """Return a trimmed ``stderr_text`` snippet up to ``limit`` characters."""
    snippet = stderr_text.strip()
    if not snippet:
        return None
    snippet = snippet.replace("\r\n", "\n").strip()
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rstrip() + "…"


def _format_isolation_error(exc: ValidationError) -> str | None:
    """Return a helpful message when sandbox dependencies are missing."""
    cause = exc.__cause__
    if not isinstance(cause, ProcessExecutionError):
        return None

    stderr_text = _decode_stream(getattr(cause, "stderr", ""))
    if not stderr_text:
        return None

    if any(
        re.search(pattern, stderr_text, re.IGNORECASE)
        for pattern in _ISOLATION_ERROR_PATTERNS
    ):
        message = (
            "polythene could not start because sandbox dependencies are missing. "
            "Install either bubblewrap (`bwrap`) or proot on the runner to enable "
            "package validation."
        )
        snippet = _stderr_snippet(stderr_text)
        if snippet is not None:
            message = (
                f"{message}\n"
                f"Original stderr (truncated to {_STDERR_SNIPPET_LIMIT} chars):\n"
                f"{snippet}"
            )
        return message

    return None


@dataclasses.dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    command: Command
    uid: str
    store: Path
    timeout: int | None = None
    isolation: str | None = None

    @property
    def root(self) -> Path:
        """Return the root filesystem path for this session."""
        return self.store / self.uid

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Execute ``args`` inside the sandbox and return its stdout."""
        effective_timeout = timeout if timeout is not None else self.timeout
        cmd_args: list[str] = [
            "run",
            *self.command,
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
        ]
        if self.isolation:
            cmd_args.extend(["--isolation", self.isolation])
        cmd_args.append("--")
        cmd_args.extend(args)

        cmd = local["uv"][tuple(cmd_args)]
        return run_text(cmd, timeout=effective_timeout)


def default_polythene_command() -> Command:
    """Return the default command tuple for invoking polythene."""
    return DEFAULT_POLYTHENE_COMMAND


@contextlib.contextmanager
def polythene_rootfs(
    polythene_command: Command,
    image: str,
    store: Path,
    *,
    timeout: int | None = None,
) -> typ.Iterator[PolytheneSession]:
    """Yield a :class:`PolytheneSession` for ``image`` using ``store``."""
    ensure_directory(store)
    pull_cmd = local["uv"][
        "run",
        *polythene_command,
        "pull",
        image,
        "--store",
        store.as_posix(),
    ]
    try:
        pull_output = run_text(pull_cmd, timeout=timeout)
    except ValidationError as exc:  # pragma: no cover - exercised in CI
        message = f"polythene pull failed: {exc}"
        raise ValidationError(message) from exc
    uid = pull_output.splitlines()[-1].strip()
    if not uid:
        message = "polythene pull returned an empty identifier"
        raise ValidationError(message)
    isolation = os.environ.get("POLYTHENE_ISOLATION") or DEFAULT_ISOLATION
    session = PolytheneSession(
        polythene_command,
        uid,
        store,
        timeout,
        isolation=isolation,
    )
    ensure_directory(session.root, exist_ok=True)
    try:
        session.exec("true")
    except ValidationError as exc:
        formatted = _format_isolation_error(exc)
        if formatted is not None:
            raise ValidationError(formatted) from exc

        cause = exc.__cause__
        if isinstance(cause, ProcessExecutionError):
            stderr = _stderr_snippet(_decode_stream(getattr(cause, "stderr", "")))
            message = f"polythene exec failed: {cause}"
            if stderr is not None:
                message = (
                    f"{message}\n"
                    f"stderr (truncated to {_STDERR_SNIPPET_LIMIT} chars):\n"
                    f"{stderr}"
                )
            raise ValidationError(message) from exc

        raise
    try:
        yield session
    finally:
        try:
            local["uv"][
                "run",
                *polythene_command,
                "rm",
                uid,
                "--store",
                store.as_posix(),
            ]()
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
            logger.debug("polythene cleanup failed: %s", exc)
