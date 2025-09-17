"""Helper package for the rust-build-release action scripts."""

from __future__ import annotations

import sys
from importlib import util
from pathlib import Path
from types import ModuleType

__all__ = ["load_sibling"]

_PACKAGE_NAME = __name__
_PACKAGE_PATH = Path(__file__).resolve().parent


def load_sibling(module: str) -> ModuleType:
    """Load a sibling module when executed outside the package context."""
    full_name = f"{_PACKAGE_NAME}.{module}"
    if _PACKAGE_NAME not in sys.modules:
        pkg = ModuleType(_PACKAGE_NAME)
        pkg.__path__ = [str(_PACKAGE_PATH)]  # type: ignore[attr-defined]
        sys.modules[_PACKAGE_NAME] = pkg
    spec = util.spec_from_file_location(full_name, _PACKAGE_PATH / f"{module}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        msg = f"Unable to load module {module!r} from {_PACKAGE_PATH}"
        raise ImportError(msg)
    module_obj = util.module_from_spec(spec)
    sys.modules[full_name] = module_obj
    spec.loader.exec_module(module_obj)
    return module_obj
