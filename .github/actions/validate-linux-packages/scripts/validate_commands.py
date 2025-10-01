"""Command execution helpers for the validate-linux-packages scripts."""

from __future__ import annotations

import importlib
import sys
import typing as typ
from pathlib import Path

# Depend on helpers exposed by the sibling linux-packages action so the
# validation workflow can reuse shared utilities (for example ``script_utils``).
SIBLING_SCRIPTS = Path(__file__).resolve().parents[2] / "linux-packages" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.append(str(SIBLING_SCRIPTS))

plumbum_base = importlib.import_module("plumbum.commands.base")
script_utils = importlib.import_module("script_utils")

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime fallback
    BaseCommand = plumbum_base.BaseCommand  # type: ignore[assignment]

run_cmd = script_utils.run_cmd

__all__ = [
    "SIBLING_SCRIPTS",
    "run_text",
]


def run_text(command: BaseCommand, *, timeout: int | None = None) -> str:
    """Execute ``command`` and return its stdout as ``str``."""

    def _coerce(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        stdout = getattr(value, "stdout", None)
        if stdout is not None and not isinstance(value, (str, bytes)):
            return _coerce(stdout)
        if isinstance(value, int):
            return ""
        return str(value)

    result = run_cmd(command, timeout=timeout)
    if isinstance(result, tuple):
        return "".join(_coerce(part) for part in result)
    return _coerce(result)
