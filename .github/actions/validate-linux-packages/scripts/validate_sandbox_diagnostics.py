"""Diagnostic collection utilities for sandbox troubleshooting."""

from __future__ import annotations

import logging
import pathlib
import stat
import typing as typ

from validate_exceptions import ValidationError
from validate_formatters import _extract_process_stderr, _trim_output

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    from validate_polythene import PolytheneSession
else:  # pragma: no cover - runtime fallback
    Path = pathlib.Path

logger = logging.getLogger(__name__)

__all__ = [
    "_build_path_diagnostic_commands",
    "_collect_diagnostics_safely",
    "_collect_environment_details",
    "_collect_host_path_details",
    "_execute_diagnostic_command",
    "_format_path_diagnostics",
]

_PATH_CHECK_TIMEOUT_SECONDS = 10


def _execute_diagnostic_command(
    sandbox: PolytheneSession, label: str, args: tuple[str, ...]
) -> str:
    """Return formatted diagnostics for executing ``args`` in ``sandbox``."""
    try:
        output = sandbox.exec(*args, timeout=_PATH_CHECK_TIMEOUT_SECONDS)
    except ValidationError as exc:
        summary = _trim_output(str(exc))
        entry_lines = [f"- {label}: error ({summary})"]
        stderr_text = _extract_process_stderr(exc.__cause__)
        if stderr_text is not None:
            entry_lines.append(f"  stderr: {stderr_text}")
        return "\n".join(entry_lines)

    summary = _trim_output(output)
    return f"- {label}: {summary}"


def _build_path_diagnostic_commands(path: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return diagnostic command specifications for ``path``."""
    commands: list[tuple[str, tuple[str, ...]]] = [
        ("ls -ld", ("ls", "-ld", path)),
        ("stat", ("stat", "-c", "%A %a %U %G %n", path)),
        ("file", ("file", path)),
        ("sha256sum", ("sha256sum", path)),
        (f"{path} --help", (path, "--help")),
    ]

    script = (
        "import os; "
        f"path={path!r}; "
        "st=os.stat(path); "
        "print('mode', oct(st.st_mode), 'uid', st.st_uid, 'gid', st.st_gid); "
        "print('x_ok', os.access(path, os.X_OK))"
    )
    commands.append(("python os.access", ("python3", "-c", script)))

    parent = str(pathlib.PurePosixPath(path).parent)
    if parent and parent != ".":
        commands.append(("ls parent", ("ls", "-l", parent)))

    return commands


def _format_path_diagnostics(
    sandbox: PolytheneSession, path: str, *, error: BaseException | None = None
) -> str | None:
    """Return sandbox diagnostics describing ``path`` when checks fail."""
    commands = _build_path_diagnostic_commands(path)

    details: list[str] = []
    stderr_text = _extract_process_stderr(error)
    if stderr_text:
        details.append(f"- stderr: {stderr_text}")

    for label, args in commands:
        result = _execute_diagnostic_command(sandbox, label, args)
        details.append(result)

    if not details:
        return None

    joined = "\n".join(details)
    return f"Path diagnostics for {path}:\n{joined}"


def _collect_diagnostics_safely(
    diagnostics_fn: typ.Callable[[BaseException | None], str | None] | None,
    args: tuple[str, ...],
    cause: BaseException | None,
) -> str | None:
    """Return diagnostics from ``diagnostics_fn`` while suppressing errors."""
    if diagnostics_fn is None:
        return None

    try:
        return diagnostics_fn(cause)
    except ValidationError as diag_exc:  # pragma: no cover - defensive
        logger.debug(
            "diagnostic collection raised ValidationError for %s: %s",
            args,
            diag_exc,
        )
    except Exception as diag_exc:  # noqa: BLE001
        logger.debug(
            "failed to collect diagnostics for %s: %s",
            args,
            diag_exc,
        )
    return None


def _collect_environment_details(sandbox: PolytheneSession) -> list[str]:
    """Return environment diagnostics gathered from ``sandbox``."""
    env_details: list[str] = []
    for label, command in (
        ("id -u", ("id", "-u")),
        ("umask", ("sh", "-c", "umask")),
        (
            "mount /usr",
            ("sh", "-c", "mount | grep ' /usr ' || true"),
        ),
    ):
        try:
            output = sandbox.exec(*command)
        except ValidationError as exc:
            summary = _trim_output(str(exc))
            env_details.append(f"{label}: error ({summary})")
        else:
            summary = _trim_output(output)
            env_details.append(f"{label}: {summary}")

    return env_details


def _collect_host_path_details(
    sandbox: PolytheneSession, paths: list[str]
) -> list[str]:
    """Return host-side ``stat`` information for ``paths`` within ``sandbox``."""
    host_details: list[str] = []
    for path in paths:
        host_path = sandbox.root / path.lstrip("/")
        try:
            stat_result = host_path.stat()
        except FileNotFoundError as err:
            host_details.append(f"{path}: missing ({err})")
        else:
            perm_bits = stat.S_IMODE(stat_result.st_mode)
            host_details.append(
                f"{path}: mode={oct(stat_result.st_mode)} "
                f"perm={oct(perm_bits)} "
                f"uid={stat_result.st_uid} gid={stat_result.st_gid}"
            )

    return host_details
