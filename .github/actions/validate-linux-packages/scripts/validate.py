#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "plumbum>=1.8,<2.0",
#   "syspath-hack>=0.2,<0.4",
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
    import importlib

    from syspath_hack import SysPathMode, ensure_module_dir

    _SCRIPT_DIR = ensure_module_dir(__file__, mode=SysPathMode.PREPEND)

    validate_cli = importlib.import_module("validate_cli")
    validate_exceptions = importlib.import_module("validate_exceptions")
    validate_metadata = importlib.import_module("validate_metadata")

    app = validate_cli.app
    main = validate_cli.main
    run = validate_cli.run
    ValidationError = validate_exceptions.ValidationError
    DebMetadata = validate_metadata.DebMetadata
    RpmMetadata = validate_metadata.RpmMetadata
    inspect_deb_package = validate_metadata.inspect_deb_package
    inspect_rpm_package = validate_metadata.inspect_rpm_package

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
