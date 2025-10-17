"""Pytest configuration for validate-linux-packages tests."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

REPO_ROOT = SCRIPTS_ROOT.parents[2]
if REPO_ROOT.name == ".github":
    REPO_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = SCRIPTS_ROOT / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_packages.py"

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from types import ModuleType
else:  # pragma: no cover - runtime fallback
    ModuleType = typ.Any


@pytest.fixture
def validate_packages_module() -> ModuleType:
    """Load the validate_packages module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_packages")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_packages", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_packages module"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
