"""Helpers for loading sibling modules when executed as standalone scripts."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
import typing as typ

_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_NAME = _PACKAGE_DIR.name
_HELPER_MODULE = "script_utils"
_QUALIFIED_HELPER = f"{_PACKAGE_NAME}.{_HELPER_MODULE}"


def _get_from_sys_modules() -> ModuleType | None:
    """Return a cached helper module if it was already imported."""
    for name in (_QUALIFIED_HELPER, _HELPER_MODULE):
        module = sys.modules.get(name)
        if isinstance(module, ModuleType):
            return module
    return None


def _ensure_package() -> ModuleType:
    """Ensure the package module exists in :data:`sys.modules`."""
    package = sys.modules.get(_PACKAGE_NAME)
    if isinstance(package, ModuleType):
        return package
    package_module = ModuleType(_PACKAGE_NAME)
    package_module.__path__ = [str(_PACKAGE_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PACKAGE_NAME] = package_module
    return package_module


def _import_via_package() -> ModuleType | None:
    """Attempt to import the helper module using the package name."""
    try:
        return importlib.import_module(_QUALIFIED_HELPER)
    except ImportError:
        return None


def _import_via_path() -> ModuleType:
    """Load the helper module directly from its file path."""
    _ensure_package()
    module_path = _PACKAGE_DIR / f"{_HELPER_MODULE}.py"
    spec = importlib.util.spec_from_file_location(_QUALIFIED_HELPER, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Unable to load {_HELPER_MODULE!r} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    loader = typ.cast(importlib.abc.Loader, spec.loader)
    sys.modules[_QUALIFIED_HELPER] = module
    sys.modules[_HELPER_MODULE] = module
    loader.exec_module(module)
    return module


def load_script_utils() -> ModuleType:
    """Return the ``script_utils`` module regardless of execution context."""
    cached = _get_from_sys_modules()
    if cached is not None:
        return cached

    module = _import_via_package()
    if module is not None:
        return module

    return _import_via_path()
