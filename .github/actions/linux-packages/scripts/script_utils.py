"""Shared helpers for rust-build-release scripts."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import typing as typ
from pathlib import Path

import typer
from plumbum import local
from syspath_hack import prepend_to_syspath, remove_from_syspath

PKG_DIR = Path(__file__).resolve().parent


class _CmdUtilsModule(typ.Protocol):
    """Typed view of the :mod:`cmd_utils` module."""

    run_cmd: typ.Callable[..., typ.Any]


CmdUtilsImporter = typ.Callable[[], _CmdUtilsModule]


def _ensure_repo_root_on_sys_path() -> Path | None:
    """Ensure the repository root containing ``cmd_utils_importer`` is importable."""
    action_path = os.environ.get("GITHUB_ACTION_PATH")
    roots: list[Path] = []
    if action_path:
        roots.append(Path(action_path).resolve())
    roots.extend([PKG_DIR, *PKG_DIR.parents])

    repo_root = next(
        (
            candidate
            for candidate in roots
            if (candidate / "cmd_utils_importer.py").is_file()
        ),
        None,
    )
    if repo_root is None:
        return None

    remove_from_syspath(repo_root)
    prepend_to_syspath(repo_root)
    return repo_root


def _resolve_import_cmd_utils() -> CmdUtilsImporter:
    """Return the ``import_cmd_utils`` callable with ``sys.path`` primed."""
    if not __package__:
        _ensure_repo_root_on_sys_path()
    from cmd_utils_importer import import_cmd_utils as importer

    return typ.cast("CmdUtilsImporter", importer)


import_cmd_utils = _resolve_import_cmd_utils()

if typ.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand

try:  # pragma: no cover - exercised during script execution
    from .cmd_utils import run_cmd
except ImportError:  # pragma: no cover - fallback when run as a script
    run_cmd = import_cmd_utils().run_cmd

__all__ = [
    "PKG_DIR",
    "ScriptHelperExports",
    "ensure_directory",
    "ensure_exists",
    "get_command",
    "load_script_helpers",
    "run_cmd",
    "unique_match",
]

PathIterable = typ.Iterable[Path]


class ScriptHelperExports(typ.NamedTuple):
    """Container for helper callables needed by standalone scripts."""

    ensure_directory: typ.Callable[..., Path]
    ensure_exists: typ.Callable[[Path, str], None]
    get_command: typ.Callable[[str], typ.Any]
    run_cmd: typ.Callable[..., typ.Any]


def _as_any(value: object) -> typ.Any:  # noqa: ANN401 - intentional escape hatch
    """Return *value* typed as ``Any`` for the benefit of static analysis."""
    return value


def load_script_helpers() -> ScriptHelperExports:
    """Return helper callables for scripts executed outside the package."""
    module_name = f"{__package__}.script_utils" if __package__ else "script_utils"
    raw_module: typ.Any | None = None
    try:
        raw_module = _as_any(importlib.import_module(module_name))
    except ImportError:  # pragma: no cover - exercised via fallback tests
        module_path = PKG_DIR / "script_utils.py"
        loader = importlib.machinery.SourceFileLoader(
            "script_utils", module_path.as_posix()
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            raise ImportError(name="script_utils") from None
        raw_module = _as_any(importlib.util.module_from_spec(spec))
        loader.exec_module(raw_module)

    if raw_module is None:  # pragma: no cover - defensive fallback
        raise ImportError(name="script_utils") from None

    helpers_mod: typ.Any = raw_module
    return ScriptHelperExports(
        helpers_mod.ensure_directory,
        helpers_mod.ensure_exists,
        helpers_mod.get_command,
        helpers_mod.run_cmd,
    )


def get_command(name: str) -> BaseCommand:
    """Return a ``plumbum`` command, exiting with an error if it is missing."""
    try:
        return local[name]
    except Exception as exc:  # pragma: no cover - error path
        typer.secho(
            f"Required command not found: {name}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(127) from exc


def ensure_exists(path: Path, message: str) -> None:
    """Exit with an error if ``path`` does not exist."""
    if not path.exists():  # pragma: no cover - defensive check
        typer.secho(f"error: {message}: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)


def ensure_directory(path: Path, *, exist_ok: bool = True) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=exist_ok)
    return path


def unique_match(paths: PathIterable, *, description: str) -> Path:
    """Return the sole path in ``paths`` or exit with an error."""
    matches = list(paths)
    if len(matches) != 1:
        typer.secho(
            f"error: expected exactly one {description}, found {len(matches)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    return matches[0]
