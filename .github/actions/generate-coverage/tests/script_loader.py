"""Import helpers for the standalone coverage scripts under test.

The action's scripts are executed as ``uv`` script entry points rather than as
an installed package, so tests load them from source with a fresh module state
for each test.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

    import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
ROOT_DIR = Path(__file__).resolve().parents[4]


def load_script_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> ModuleType:
    """Import ``name`` from the ``scripts`` directory with real dependencies.

    The scripts are ``uv`` entry points rather than installed modules, so they
    are loaded from source by path. ``SCRIPT_DIR`` and ``ROOT_DIR`` are pushed
    onto ``sys.path`` through ``monkeypatch`` so the script's own sibling
    imports resolve, and both ``name`` and ``coverage_parsers`` are evicted
    from ``sys.modules`` before the import so the caller receives freshly
    executed module state rather than a cached instance from an earlier test.
    ``importlib.invalidate_caches`` is called for the same reason.

    Because ``monkeypatch`` owns the ``sys.path`` and ``sys.modules`` edits,
    they are reverted when the requesting test ends. The returned module itself
    is not registered in ``sys.modules``, so each call yields an independent
    object whose module-level state — including import-time side effects — can
    be mutated without affecting other tests.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to scope the ``sys.path`` and ``sys.modules`` changes to
        the calling test.
    name : str
        Module name to import, matching a ``<name>.py`` file in ``SCRIPT_DIR``
        (for example ``"run_rust"`` or ``"run_python"``).

    Returns
    -------
    ModuleType
        The freshly executed module.

    Raises
    ------
    RuntimeError
        If no import spec or loader can be built for ``name``, which usually
        means the script file is missing from ``SCRIPT_DIR``.

    Examples
    --------
    Expose a script as a fixture so every test gets untouched module state::

        @pytest.fixture
        def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
            return load_script_module(monkeypatch, "run_rust")

        def test_workspace_is_default(run_rust_module: ModuleType) -> None:
            args = run_rust_module.get_cargo_coverage_cmd(...)
            assert "--workspace" in args
    """
    monkeypatch.syspath_prepend(SCRIPT_DIR)
    monkeypatch.syspath_prepend(ROOT_DIR)
    for module_name in (name, "coverage_parsers"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        message = f"unable to load script module {name!r} from {SCRIPT_DIR}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
