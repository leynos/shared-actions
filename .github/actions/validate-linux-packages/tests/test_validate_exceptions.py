"""Tests for custom validation exceptions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_exceptions.py"


def test_validation_error_inherits_runtime_error() -> None:
    """ValidationError should behave like a RuntimeError subclass."""
    spec = importlib.util.spec_from_file_location("validate_exceptions", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("unable to load validate_exceptions module")

    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    err = module.ValidationError("failure")

    assert isinstance(err, RuntimeError)
    assert str(err) == "failure"
