"""Polythene sandbox helpers for package validation."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import typing as typ
from pathlib import Path

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from validate_commands import run_text
from validate_exceptions import ValidationError
from validate_helpers import ensure_directory

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
LINUX_PACKAGES_SCRIPTS = SCRIPT_DIR.parent.parent / "linux-packages" / "scripts"

__all__ = [
    "PolytheneSession",
    "SandboxUnavailableError",
    "default_polythene_path",
    "polythene_rootfs",
]


class SandboxUnavailableError(ValidationError):
    """Raised when the polythene sandbox cannot provide any isolation."""


def _is_sandbox_unavailable(error: ValidationError) -> bool:
    """Return ``True`` when ``error`` indicates the sandbox lacks backends."""

    cause = error.__cause__
    return isinstance(cause, ProcessExecutionError) and cause.retcode == 126


@dataclasses.dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    script: Path
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
        cmd = local[
            "uv",
            "run",
            self.script.as_posix(),
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
            "--",
            *args,
        ]
        try:
            return run_text(cmd, timeout=effective_timeout)
        except ValidationError as exc:
            if _is_sandbox_unavailable(exc):
                message = (
                    "polythene sandbox unavailable; install bubblewrap or proot to "
                    "enable package validation"
                )
                raise SandboxUnavailableError(message) from exc
            raise


def default_polythene_path() -> Path:
    """Return the default path to the polythene helper script."""
    return LINUX_PACKAGES_SCRIPTS / "polythene.py"


@contextlib.contextmanager
def polythene_rootfs(
    polythene: Path,
    image: str,
    store: Path,
    *,
    timeout: int | None = None,
) -> typ.ContextManager[PolytheneSession]:
    """Yield a :class:`PolytheneSession` for ``image`` using ``store``."""
    ensure_directory(store)
    pull_cmd = local[
        "uv",
        "run",
        polythene.as_posix(),
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
    session = PolytheneSession(polythene, uid, store, timeout)
    ensure_directory(session.root, exist_ok=True)
    try:
        session.exec("true")
    except SandboxUnavailableError:
        raise
    except ValidationError as exc:  # pragma: no cover - exercised in CI
        message = f"polythene exec failed: {exc}"
        raise ValidationError(message) from exc
    try:
        yield session
    finally:
        try:
            local[
                "uv",
                "run",
                polythene.as_posix(),
                "rm",
                uid,
                "--store",
                store.as_posix(),
            ]()
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
            logger.debug("polythene cleanup failed: %s", exc)
