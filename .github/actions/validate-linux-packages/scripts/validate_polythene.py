"""Helpers for interacting with the polythene sandbox CLI."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
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

__all__ = sorted(
    (
        "Command",
        "DEFAULT_POLYTHENE_COMMAND",
        "PolytheneSession",
        "default_polythene_command",
        "polythene_rootfs",
    )
)


@dataclasses.dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    command: Command
    uid: str
    store: Path
    timeout: int | None = None

    @property
    def root(self) -> Path:
        """Return the root filesystem path for this session."""
        return self.store / self.uid

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Execute ``args`` inside the sandbox and return its stdout."""
        effective_timeout = timeout if timeout is not None else self.timeout
        cmd = local["uv"][
            "run",
            *self.command,
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
            "--",
            *args,
        ]
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
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        message = f"polythene pull failed: {exc}"
        raise ValidationError(message) from exc
    uid = pull_output.splitlines()[-1].strip()
    if not uid:
        message = "polythene pull returned an empty identifier"
        raise ValidationError(message)
    session = PolytheneSession(polythene_command, uid, store, timeout)
    ensure_directory(session.root, exist_ok=True)
    try:
        session.exec("true")
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        message = f"polythene exec failed: {exc}"
        raise ValidationError(message) from exc
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
