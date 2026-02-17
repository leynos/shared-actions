"""Output formatting utilities for package validation diagnostics."""

from __future__ import annotations

import textwrap

from plumbum.commands.processes import ProcessExecutionError
from validate_polythene import _decode_stream

__all__ = [
    "_extract_process_stderr",
    "_trim_output",
    "_trim_output_single_line",
]


def _trim_output(output: str, *, line_limit: int = 5, char_limit: int = 400) -> str:
    """Return ``output`` trimmed to a manageable length for diagnostics."""
    text = output.strip()
    if not text:
        return "<no output>"

    lines = text.splitlines()
    if len(lines) > line_limit:
        text = "\n".join(lines[:line_limit]) + "\n…"
    else:
        text = "\n".join(lines)

    if len(text) > char_limit:
        text = text[: char_limit - 1].rstrip() + "…"

    return text


def _trim_output_single_line(text: str, char_limit: int = 200) -> str:
    """Return a trimmed single-line summary suitable for logging."""
    normalized = " ".join(text.split())
    if len(normalized) <= char_limit:
        return normalized
    return textwrap.shorten(normalized, width=char_limit, placeholder="…")


def _extract_process_stderr(error: BaseException | None) -> str | None:
    """Return trimmed stderr output when ``error`` originated from a process."""
    if not isinstance(error, ProcessExecutionError):
        return None

    stderr_text = _decode_stream(getattr(error, "stderr", None))
    if not stderr_text.strip():
        return None
    return _trim_output(stderr_text)
