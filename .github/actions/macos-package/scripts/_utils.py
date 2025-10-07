"""Helpers shared across the macOS packaging action scripts."""

from __future__ import annotations

import sys
import typing as typ
from os import environ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

if typ.TYPE_CHECKING:
    import collections.abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)

__all__ = [
    "ActionError",
    "Parameter",
    "action_work_dir",
    "append_key_value",
    "configure_app",
    "ensure_regular_file",
    "log_warning",
    "remove_file",
    "run_app",
    "write_env",
    "write_output",
]


class ActionError(RuntimeError):
    """Raised when an action script encounters a user-facing error."""


def configure_app() -> App:
    """Return a Cyclopts app configured for GitHub Actions inputs."""
    app = App()
    app.config = cyclopts.config.Env("INPUT_", command=False)
    return app


def _emit_error(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def run_app(app: App, *, argv: cabc.Sequence[str] | None = None) -> None:
    """Execute ``app`` and present user-facing errors consistently."""
    if argv is None:
        if "PYTEST_CURRENT_TEST" in environ:
            tokens: list[str] = []
        else:
            tokens = list(sys.argv[1:])
    else:
        tokens = list(argv)

    invocation = tokens if tokens else []

    try:
        app(invocation)
    except (ActionError, FileNotFoundError, ValueError) as exc:
        _emit_error(str(exc))
        raise SystemExit(1) from exc


def log_warning(message: str) -> None:
    """Emit a warning message to stderr."""
    sys.stderr.write(f"Warning: {message}\n")


def action_work_dir() -> Path:
    """Return the workspace-local directory used for intermediate artefacts."""
    work_dir = Path.cwd() / ".macos-package"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def append_key_value(path: Path, key: str, value: str) -> None:
    """Append a ``key=value`` pair to the given file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def ensure_regular_file(path: Path, description: str) -> Path:
    """Return ``path`` when it references an existing regular file."""
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        msg = f"{description} not found: {resolved}"
        raise ActionError(msg)
    if resolved.is_dir():
        msg = f"{description} is a directory: {resolved}"
        raise ActionError(msg)
    if not resolved.is_file():
        msg = f"{description} is not a regular file: {resolved}"
        raise ActionError(msg)
    return resolved


def remove_file(path: Path, *, warn: bool = True, context: str | None = None) -> None:
    """Remove ``path`` if it exists, optionally logging failures as warnings."""
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError as exc:
        if warn:
            descriptor = context or f"file '{path}'"
            log_warning(f"Could not remove existing {descriptor}: {exc}")
        else:  # pragma: no cover - debug aid
            raise


def write_output(key: str, value: str) -> None:
    """Write an output variable for the current GitHub step."""
    from os import environ

    if output_path := environ.get("GITHUB_OUTPUT"):
        append_key_value(Path(output_path), key, value)


def write_env(key: str, value: str) -> None:
    """Write an environment variable for subsequent GitHub steps."""
    from os import environ

    if env_path := environ.get("GITHUB_ENV"):
        append_key_value(Path(env_path), key, value)
