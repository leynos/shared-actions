from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
PKG_NAME = f"{PKG_DIR.parent.name.replace('-', '')}_scripts"


def bootstrap_package() -> types.ModuleType:
    """Ensure the scripts package is registered and return it."""
    pkg_module = sys.modules.get(PKG_NAME)
    if pkg_module is None:
        pkg_module = types.ModuleType(PKG_NAME)
        pkg_module.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules[PKG_NAME] = pkg_module
    if not hasattr(pkg_module, "load_sibling"):
        spec = importlib.util.spec_from_file_location(PKG_NAME, PKG_DIR / "__init__.py")
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ImportError(name=PKG_NAME) from None
        module = importlib.util.module_from_spec(spec)
        sys.modules[PKG_NAME] = module
        spec.loader.exec_module(module)
        pkg_module = module
    return pkg_module


def load_helper_module(name: str) -> types.ModuleType:
    """Load a helper module from the scripts package via ``load_sibling``."""
    package = bootstrap_package()
    return package.load_sibling(name)  # type: ignore[no-any-return]


__all__ = ["PKG_DIR", "PKG_NAME", "bootstrap_package", "load_helper_module"]
