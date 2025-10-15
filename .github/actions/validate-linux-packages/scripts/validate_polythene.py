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


@dataclasses.dataclass(slots=True)
class _CommandArgsResult:
    """Return type for :func:`_build_command_args`."""

    args: list[str]
    used_isolation: bool


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


def _is_unknown_isolation_option_error(exc: ValidationError) -> bool:
    """Return ``True`` if ``exc`` was raised due to an unsupported flag."""
    cause = exc.__cause__
    if not isinstance(cause, ProcessExecutionError):
        return False

    stderr_text = _decode_stream(getattr(cause, "stderr", ""))
    stdout_text = _decode_stream(getattr(cause, "stdout", ""))
    combined = "\n".join(part for part in (stderr_text, stdout_text) if part)
    if not combined:
        return False
    return bool(
        re.search(
            r"(unknown\s+option[^\n]*--isolation|--isolation[^\n]*unknown\s+option)",
            combined,
            flags=re.IGNORECASE,
        )
    )


def _build_command_args(
    base_args: list[str],
    args: tuple[str, ...],
    isolation: str | None,
    *,
    supports_isolation: bool,
) -> _CommandArgsResult:
    """Return ``cmd`` arguments and whether ``--isolation`` should be included."""
    include_isolation = bool(isolation) and supports_isolation
    if include_isolation:
        return _CommandArgsResult(
            args=[
                *base_args,
                "--isolation",
                isolation,
                "--",
                *args,
            ],
            used_isolation=True,
        )
    return _CommandArgsResult(args=[*base_args, "--", *args], used_isolation=False)


def _should_compare_without_isolation(args: tuple[str, ...]) -> bool:
    """Return ``True`` when ``args`` represent an executable check."""
    return len(args) >= 3 and args[0] == "test" and args[1] == "-x"


def _compare_without_isolation(
    base_args: list[str], args: tuple[str, ...], *, timeout: int | None
) -> None:
    """Run ``args`` without isolation and log the outcome for diagnostics."""
    debug_args = _build_command_args(
        base_args,
        args,
        isolation="none",
        supports_isolation=True,
    )
    debug_cmd = local["uv"][tuple(debug_args.args)]
    try:
        run_text(debug_cmd, timeout=timeout)
    except ValidationError as exc:  # pragma: no cover - diagnostic helper
        logger.debug(
            "no-isolation comparison for %s failed: %s",
            " ".join(args),
            exc,
        )
    else:
        logger.debug(
            "no-isolation comparison for %s succeeded",
            " ".join(args),
        )


@dataclasses.dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    command: Command
    uid: str
    store: Path
    timeout: int | None = None
    isolation: str | None = None
    _supports_isolation_option: bool | None = dataclasses.field(
        # Cache for ``--isolation`` support: ``None`` unknown, ``True`` supported,
        # ``False`` unsupported.
        default=None,
        init=False,
        repr=False,
    )

    @property
    def root(self) -> Path:
        """Return the root filesystem path for this session."""
        return self.store / self.uid

    def _retry_without_isolation(
        self,
        base_args: list[str],
        args: tuple[str, ...],
        timeout: int | None,
    ) -> str:
        """Retry ``args`` without ``--isolation`` when the flag is unsupported."""
        logger.info(
            "Isolation flag unsupported; retrying without --isolation for %s",
            self.uid,
        )
        self._supports_isolation_option = False
        fallback_args = _build_command_args(
            base_args,
            args,
            isolation=None,
            supports_isolation=False,
        )
        fallback_cmd = local["uv"][tuple(fallback_args.args)]
        return run_text(fallback_cmd, timeout=timeout)

    def _handle_exec_error(
        self,
        exc: ValidationError,
        base_args: list[str],
        args: tuple[str, ...],
        timeout: int | None,
        *,
        include_isolation: bool,
    ) -> str:
        """Handle ``ValidationError`` raised by ``exec`` attempts."""
        if include_isolation and _is_unknown_isolation_option_error(exc):
            return self._retry_without_isolation(base_args, args, timeout)
        if include_isolation and _should_compare_without_isolation(args):
            _compare_without_isolation(base_args, args, timeout=timeout)
        raise exc

    def _record_isolation_support(self, *, include_isolation: bool) -> None:
        """Record that ``--isolation`` is supported when the call succeeds."""
        if include_isolation and self._supports_isolation_option is not True:
            self._supports_isolation_option = True

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Execute ``args`` inside the sandbox and return its stdout."""
        effective_timeout = timeout if timeout is not None else self.timeout
        base_args: list[str] = [
            "run",
            *self.command,
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
        ]
        supports_isolation = self._supports_isolation_option is not False
        command_args = _build_command_args(
            base_args,
            args,
            self.isolation,
            supports_isolation=supports_isolation,
        )
        cmd_args = command_args.args
        include_isolation = command_args.used_isolation

        cmd = local["uv"][tuple(cmd_args)]
        try:
            result = run_text(cmd, timeout=effective_timeout)
        except ValidationError as exc:
            return self._handle_exec_error(
                exc,
                base_args,
                args,
                effective_timeout,
                include_isolation=include_isolation,
            )
        else:
            self._record_isolation_support(include_isolation=include_isolation)
            return result


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
        fallback_exc = exc
        fallback_succeeded = False
        cause = exc.__cause__

        if (
            session.isolation
            and session.isolation != DEFAULT_ISOLATION
            and isinstance(cause, ProcessExecutionError)
        ):
            stderr_text = _decode_stream(getattr(cause, "stderr", ""))
            if re.search(r"permission denied", stderr_text, re.IGNORECASE):
                logger.info(
                    "Isolation %s failed with permission denied; falling back to %s",
                    session.isolation,
                    DEFAULT_ISOLATION,
                )
                session.isolation = DEFAULT_ISOLATION
                try:
                    session.exec("true")
                except ValidationError as retry_exc:  # pragma: no cover - fallback path
                    fallback_exc = retry_exc
                else:
                    fallback_succeeded = True

        if fallback_succeeded:
            logger.info("Fallback to %s isolation succeeded", DEFAULT_ISOLATION)
        else:
            exc = fallback_exc
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
