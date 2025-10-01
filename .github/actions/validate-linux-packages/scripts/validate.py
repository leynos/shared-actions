#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "plumbum>=1.8,<2.0",
#   "typer>=0.9,<1.0",
# ]
# ///

"""Validate Linux packages by inspecting metadata and running sandboxed installs."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

SIBLING_SCRIPTS = SCRIPT_DIR.parent.parent / "linux-packages" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.append(str(SIBLING_SCRIPTS))

from validate_cli import app, main, run  # noqa: E402  (import after sys.path mutation)
from validate_exceptions import ValidationError  # noqa: E402
from validate_metadata import (  # noqa: E402
    DebMetadata,
    RpmMetadata,
    inspect_deb_package,
    inspect_rpm_package,
)

__all__ = [
    "DebMetadata",
    "RpmMetadata",
    "ValidationError",
    "app",
    "inspect_deb_package",
    "inspect_rpm_package",
    "main",
    "run",
]


if __name__ == "__main__":  # pragma: no cover - script execution
    run()
