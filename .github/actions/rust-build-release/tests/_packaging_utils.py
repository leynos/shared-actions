"""Re-export the linux-packages packaging test helpers.

The packaging fixtures live with the linux-packages tests, but the
rust-build-release suite needs the same sample project and artefact builders.
The tests directories are not packages, so the module is loaded by path and its
public names are rebound here rather than imported.
"""

from __future__ import annotations

import importlib.util as _ilus
import sys as _sys
from pathlib import Path as _Path

_ERR_RESOLVE = "failed to resolve packaging utils module"

_tests_root = _Path(__file__).resolve().parents[2] / "linux-packages" / "tests"
_mod_path = _tests_root / "_packaging_utils.py"
_spec = _ilus.spec_from_file_location("linux_packages_packaging_utils", _mod_path)
if _spec is None or _spec.loader is None:
    raise ImportError(_ERR_RESOLVE)
_mod = _ilus.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

DEFAULT_CONFIG = _mod.DEFAULT_CONFIG
DEFAULT_TARGET = _mod.DEFAULT_TARGET
BuildArtefacts = _mod.BuildArtefacts
PackagingConfig = _mod.PackagingConfig
PackagingProject = _mod.PackagingProject
build_release_artefacts = _mod.build_release_artefacts
package_project = _mod.package_project
packaging_project = _mod.packaging_project
