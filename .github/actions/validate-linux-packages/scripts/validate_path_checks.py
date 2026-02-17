"""Path existence and executability validation for sandbox environments."""

from __future__ import annotations

import logging
import typing as typ

from validate_exceptions import ValidationError
from validate_formatters import _trim_output_single_line
from validate_sandbox_diagnostics import _format_path_diagnostics

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from validate_polythene import PolytheneSession

logger = logging.getLogger(__name__)

__all__ = [
    "_PATH_CHECK_TIMEOUT_SECONDS",
    "_combine_fallback_errors",
    "_iter_python_fallback_commands",
    "_make_path_diagnostics_fn",
    "_try_python_fallback",
    "_validate_paths_executable",
    "_validate_paths_exist",
]

_PYTHON_FALLBACK_SCRIPT = (
    "import os, sys\nsys.exit(0 if os.access(sys.argv[1], os.X_OK) else 1)\n"
)
_PYTHON_FALLBACK_INTERPRETERS: tuple[str, ...] = ("python3", "python")
_PATH_CHECK_TIMEOUT_SECONDS = 10


def _validate_paths_exist(
    sandbox: PolytheneSession,
    paths: typ.Iterable[str],
    exec_fn: typ.Callable[..., str],
) -> None:
    """Ensure each path in ``paths`` exists within ``sandbox``."""
    for path in paths:
        exec_fn(
            "test",
            "-e",
            path,
            context=f"expected path missing from sandbox payload: {path}",
            diagnostics_fn=lambda err, p=path: _format_path_diagnostics(
                sandbox, p, error=err
            ),
        )


def _make_path_diagnostics_fn(
    sandbox: PolytheneSession, path: str
) -> typ.Callable[[BaseException | None], str | None]:
    def _diagnostics(error: BaseException | None) -> str | None:
        return _format_path_diagnostics(sandbox, path, error=error)

    return _diagnostics


def _iter_python_fallback_commands(path: str) -> typ.Iterator[tuple[str, ...]]:
    for interpreter in _PYTHON_FALLBACK_INTERPRETERS:
        yield (interpreter, "-c", _PYTHON_FALLBACK_SCRIPT, path)


def _combine_fallback_errors(
    primary_error: ValidationError,
    fallback_failures: list[tuple[str, ValidationError]],
) -> str:
    message_lines = [str(primary_error)]
    for interpreter, error in fallback_failures:
        heading = (
            "python os.access fallback"
            f" ({interpreter}) also reported the path as non-executable:"
        )
        message_lines.append(heading)
        message_lines.append(str(error))
    return "\n".join(message_lines)


def _try_python_fallback(
    path: str,
    exec_fn: typ.Callable[..., str],
    diagnostics_fn: typ.Callable[[BaseException | None], str | None],
    primary_error: ValidationError,
) -> None:
    """Attempt Python fallback validation; raise ValidationError if all fail."""
    fallback_failures: list[tuple[str, ValidationError]] = []

    for command in _iter_python_fallback_commands(path):
        interpreter = command[0]
        try:
            exec_fn(
                *command,
                context=(
                    "expected path is not executable "
                    "(python os.access fallback): "
                    f"{path}"
                ),
                diagnostics_fn=diagnostics_fn,
                timeout=_PATH_CHECK_TIMEOUT_SECONDS,
            )
        except ValidationError as fallback_exc:
            fallback_failures.append((interpreter, fallback_exc))
            continue
        else:
            summary = _trim_output_single_line(str(primary_error))
            logger.info(
                "test -x reported %s as non-executable; %s os.access "
                "fallback succeeded",
                path,
                interpreter,
            )
            logger.debug("test -x failure details for %s: %s", path, summary)
            return

    # All fallbacks failed
    if not fallback_failures:
        raise ValidationError(str(primary_error)) from primary_error
    combined_message = _combine_fallback_errors(primary_error, fallback_failures)
    raise ValidationError(combined_message) from fallback_failures[-1][1]


def _validate_paths_executable(
    sandbox: PolytheneSession,
    paths: typ.Iterable[str],
    exec_fn: typ.Callable[..., str],
) -> None:
    """Ensure each path in ``paths`` is executable within ``sandbox``."""
    for path in paths:
        diagnostics_fn = _make_path_diagnostics_fn(sandbox, path)
        try:
            exec_fn(
                "test",
                "-x",
                path,
                context=f"expected path is not executable: {path}",
                diagnostics_fn=diagnostics_fn,
                timeout=_PATH_CHECK_TIMEOUT_SECONDS,
            )
        except ValidationError as exc:
            _try_python_fallback(path, exec_fn, diagnostics_fn, exc)
