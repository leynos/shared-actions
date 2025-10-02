#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "plumbum>=1.8,<2.0",
# ]
# ///

"""Validate Linux packages by inspecting metadata and running sandboxed installs."""

from __future__ import annotations

if __package__:
    from .validate_cli import app, main, run
    from .validate_exceptions import ValidationError
    from .validate_metadata import (
        DebMetadata,
        RpmMetadata,
        inspect_deb_package,
        inspect_rpm_package,
    )
else:  # pragma: no cover - exercised via CLI execution
    from validate_cli import app, main, run
    from validate_exceptions import ValidationError
    from validate_metadata import (
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
