"""Unit and behavioural tests for the script helper bootstrap."""

from __future__ import annotations

import importlib
import sys
import typing as typ
from pathlib import Path

import pytest
import script_utils
import typer


def test_script_helper_exports_preserves_wrapped_callables(tmp_path: Path) -> None:
    """The ScriptHelperExports tuple forwards to the wrapped callables."""
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _record(name: str) -> typ.Callable[..., object]:
        def _inner(*args: object, **kwargs: object) -> object:
            calls.append((name, args, kwargs))
            if name == "ensure_directory":
                return args[0]
            if name == "get_command":
                return f"cmd:{args[0]}"
            return None

        return _inner

    exports = script_utils.ScriptHelperExports(
        _record("ensure_directory"),
        _record("ensure_exists"),
        _record("get_command"),
        _record("run_cmd"),
    )

    target = tmp_path / "artifact"
    result = exports.ensure_directory(target, exist_ok=False)
    assert result == target

    exports.ensure_exists(target, "must exist")
    command = exports.get_command("nfpm")
    assert command == "cmd:nfpm"

    exports.run_cmd("nfpm", "--version")

    recorded_names = [name for name, *_ in calls]
    assert recorded_names == [
        "ensure_directory",
        "ensure_exists",
        "get_command",
        "run_cmd",
    ]


def test_load_script_helpers_prefers_package_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the scripts package is importable, helpers come from that module."""
    scripts_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(scripts_root))

    preserved_modules = {
        key: sys.modules.get(key)
        for key in ("scripts", "scripts.script_utils", "script_utils")
    }
    for key in ("scripts", "scripts.script_utils"):
        sys.modules.pop(key, None)

    module = importlib.import_module("scripts.script_utils")
    try:
        reloaded = importlib.reload(module)
        helpers = reloaded.load_script_helpers()
        assert helpers.ensure_directory is reloaded.ensure_directory
        assert helpers.ensure_exists is reloaded.ensure_exists
        assert helpers.get_command is reloaded.get_command
        assert helpers.run_cmd is reloaded.run_cmd
    finally:
        for key, value in preserved_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def test_load_script_helpers_uses_loader_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ImportError triggers the SourceFileLoader fallback path."""
    module = importlib.import_module("script_utils")

    attempted: list[str] = []

    def _raising_import(name: str, package: str | None = None) -> typ.NoReturn:
        attempted.append(name)
        raise ImportError(name)

    monkeypatch.setattr(module.importlib, "import_module", _raising_import)

    seen_paths: list[Path] = []
    original_loader = module.importlib.machinery.SourceFileLoader

    class RecordingLoader(original_loader):
        def __init__(self, fullname: str, path: str) -> None:
            seen_paths.append(Path(path))
            super().__init__(fullname, path)

    monkeypatch.setattr(module.importlib.machinery, "SourceFileLoader", RecordingLoader)

    helpers = module.load_script_helpers()

    assert attempted == ["script_utils"]
    assert seen_paths
    assert seen_paths[0] == module.PKG_DIR / "script_utils.py"

    created = tmp_path / "demo"
    result = helpers.ensure_directory(created)
    assert result == created
    assert created.is_dir()

    existing = tmp_path / "existing"
    existing.write_text("ok", encoding="utf-8")
    helpers.ensure_exists(existing, "existing path")

    with pytest.raises(typer.Exit):
        helpers.ensure_exists(tmp_path / "missing", "missing path")
