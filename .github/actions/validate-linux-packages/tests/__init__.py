"""Validate linux-packages tests package."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PARENT = _PACKAGE_ROOT.parent
_REPO_ROOT = _PACKAGE_ROOT.parents[4]

for path in (str(_PARENT), str(_REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

_validate_linux_packages = importlib.import_module(
    "test_support.validate_linux_packages"
)

sys.modules.setdefault("tests.validate_linux_packages", _validate_linux_packages)

validate_linux_packages = _validate_linux_packages

__all__ = ["validate_linux_packages"]
