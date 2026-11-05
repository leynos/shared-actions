"""Configure Windows standard streams for UTF-8 output."""

from __future__ import annotations

import io
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _log_windows_stream_debug(debug: str | None, message: str, *args: object) -> None:
    """Emit ``message`` at DEBUG level only when ``debug`` is set."""
    if debug:
        logger.debug(message, *args)


def _try_reconfigure_windows_stream(
    stream: object, name: str, debug: str | None
) -> bool:
    """Reconfigure ``stream`` to UTF-8 in place, reporting whether it worked."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (
        AttributeError,
        ValueError,
        io.UnsupportedOperation,
        OSError,
    ) as exc:
        _log_windows_stream_debug(debug, "Failed to reconfigure %s: %s", name, exc)
        return False
    return True


def _try_stream_operation(
    operation: object, operation_name: str, name: str, debug: str | None
) -> bool:
    """Run an available stream operation, logging failures in debug mode."""
    if not callable(operation):
        return True
    try:
        operation()
    except (AttributeError, ValueError, io.UnsupportedOperation, OSError) as exc:
        _log_windows_stream_debug(
            debug, "Failed to %s %s: %s", operation_name, name, exc
        )
        return False
    return True


def _try_wrap_windows_stream(stream: object, name: str, debug: str | None) -> None:
    """Replace ``sys.<name>`` with a UTF-8 wrapper around the stream buffer."""
    buf = getattr(stream, "buffer", None)
    if buf is None:
        _log_windows_stream_debug(debug, "%s has no buffer; leaving as-is", name)
        return
    flush = getattr(stream, "flush", None)
    if not _try_stream_operation(flush, "flush", name, debug):
        return

    detach = getattr(stream, "detach", None)
    has_detach = callable(detach)
    if not _try_stream_operation(detach, "detach", name, debug):
        return

    try:
        wrapped = io.TextIOWrapper(
            buf,
            encoding="utf-8",
            errors="replace",
            write_through=True,
        )
    except (ValueError, OSError) as exc:
        _log_windows_stream_debug(debug, "Failed to wrap %s: %s", name, exc)
        if has_detach:
            raise
        return

    setattr(sys, name, wrapped)


def _configure_windows_standard_streams() -> None:
    """Force UTF-8 encoding on the Windows standard output and error streams."""
    debug = os.getenv("RUN_RUST_DEBUG")
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if not _try_reconfigure_windows_stream(stream, name, debug):
            _try_wrap_windows_stream(stream, name, debug)


def _configure_windows_standard_streams_on_windows() -> None:
    """Configure UTF-8 standard streams when this process runs on Windows."""
    if os.name == "nt":
        _configure_windows_standard_streams()
