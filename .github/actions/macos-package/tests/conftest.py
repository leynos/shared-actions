"""Shared pytest fixtures for the macOS packaging action scripts."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def scripts_dir() -> Path:
    """Return the path to the macOS packaging action scripts."""
    return SCRIPTS_DIR


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root."""
    return REPO_ROOT


@pytest.fixture
def load_module(monkeypatch: pytest.MonkeyPatch) -> cabc.Callable[[str], object]:
    """Return a loader that imports a script module with fresh state."""

    def _load(name: str) -> object:
        module_name = f"macos_package_{name}"
        script_path = SCRIPTS_DIR / f"{name}.py"
        if not script_path.exists():
            msg = f"Unknown script: {name}"
            raise FileNotFoundError(msg)

        monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
        monkeypatch.syspath_prepend(str(REPO_ROOT))
        for candidate in (module_name, name, "_utils"):
            monkeypatch.delitem(sys.modules, candidate, raising=False)

        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            msg = f"Failed to load module specification for {name}"
            raise RuntimeError(msg)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture(autouse=True)
def _reset_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure Cyclopts parsers do not inherit pytest's CLI arguments."""
    monkeypatch.setattr(sys, "argv", ["uv"])


@pytest.fixture
def gh_output_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Set ``GITHUB_ENV``/``GITHUB_OUTPUT`` to files within ``tmp_path``."""
    env_file = tmp_path / "github.env"
    output_file = tmp_path / "github.out"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    return env_file, output_file
