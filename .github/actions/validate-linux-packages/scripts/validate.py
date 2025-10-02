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

import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _load_local_module(name: str) -> object:
    """Load the helper module named ``name`` from the sibling scripts directory."""
    module = sys.modules.get(name)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive fallback
        message = f"unable to load helper module: {name}"
        raise ImportError(message)

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import_attribute(module: object, attribute: str) -> object:
    """Return ``attribute`` from ``module`` with a helpful error on failure."""
    try:
        return getattr(module, attribute)
    except AttributeError as exc:  # pragma: no cover - defensive fallback
        message = (
            f"missing attribute {attribute!r} on helper module {module.__name__!r}"
        )
        raise ImportError(message) from exc


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
    _cli_module = _load_local_module("validate_cli")
    app = _import_attribute(_cli_module, "app")
    main = _import_attribute(_cli_module, "main")
    run = _import_attribute(_cli_module, "run")

    _exceptions = _load_local_module("validate_exceptions")
    ValidationError = _import_attribute(_exceptions, "ValidationError")

    _metadata = _load_local_module("validate_metadata")
    DebMetadata = _import_attribute(_metadata, "DebMetadata")
    RpmMetadata = _import_attribute(_metadata, "RpmMetadata")
    inspect_deb_package = _import_attribute(_metadata, "inspect_deb_package")
    inspect_rpm_package = _import_attribute(_metadata, "inspect_rpm_package")

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
