"""Command execution helpers for the validate-linux-packages scripts."""

from __future__ import annotations

import sys
from pathlib import Path

SIBLING_SCRIPTS = Path(__file__).resolve().parents[2] / "linux-packages" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.append(str(SIBLING_SCRIPTS))

from plumbum.commands.base import BaseCommand

from script_utils import run_cmd

__all__ = ["run_text", "SIBLING_SCRIPTS"]


def run_text(command: BaseCommand, *, timeout: int | None = None) -> str:
    """Execute ``command`` and return its stdout as ``str``."""

    result = run_cmd(command, timeout=timeout)
    if isinstance(result, tuple):
        return "".join(str(part) for part in result if part is not None)
    return "" if isinstance(result, int) else str(result)
