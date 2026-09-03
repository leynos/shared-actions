"""Helpers shared by the coverage-script test modules.

``test_scripts.py``, ``test_install_cargo_nextest.py``, and
``test_install_cargo_nextest_install.py`` each load one of the action's scripts
from disk under a fresh module identity and inspect Typer exits, so those
helpers live here rather than being duplicated across the modules. Fixtures
that wrap ``_load_module`` for a specific script, such as
``install_nextest_module``, live in ``conftest.py`` instead when more than one
module needs them, so importing the fixture by name does not read as an unused
import shadowed by same-named test parameters.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

from plumbum import local

from cmd_utils_importer import import_cmd_utils
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from types import ModuleType

    import pytest

    from cmd_utils import RunResult
else:
    RunResult = import_cmd_utils().RunResult


def _exit_code(exc: BaseException) -> int | None:
    """Extract an exit code from Typer or SystemExit exceptions."""
    exit_code = getattr(exc, "exit_code", None)
    if exit_code is None:
        exit_code = getattr(exc, "code", None)
    return exit_code


def _load_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> ModuleType:
    """Import ``name`` from the ``scripts`` directory with real dependencies."""
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    root_dir = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(script_dir)
    monkeypatch.syspath_prepend(root_dir)
    for module_name in (name, "coverage_parsers"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    import importlib as _importlib  # ensure fresh module state for reloads

    _importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, script_dir / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - import failure.
        message = f"could not load {name} from {script_dir}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(script: Path, env: dict[str, str], *args: str) -> RunResult:
    """Run ``script`` with ``env`` and return the result.

    Parameters
    ----------
    script : Path
        Absolute path to the Python script to execute.
    env : dict[str, str]
        Environment variables to merge on top of the current environment.
    *args : str
        Additional positional arguments appended to the Python command.

    Returns
    -------
    RunResult
        A three-tuple of ``(return_code, stdout, stderr)``.

    Raises
    ------
    None
        This helper does not raise for non-zero exits; failures are conveyed
        via the returned exit code and stderr captured from the child process.
    """
    command = local[sys.executable][str(script)]
    if args:
        command = command[list(args)]
    root = Path(__file__).resolve().parents[4]
    merged = {**os.environ, **env}
    current_pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = (
        f"{root}{os.pathsep}{current_pp}" if current_pp else str(root)
    )
    merged["PYTHONIOENCODING"] = "utf-8"
    return run_plumbum_command(command, method="run", env=merged)
