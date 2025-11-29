#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["plumbum", "syspath-hack>=0.2,<0.4", "typer>=0.17,<0.18"]
# ///
"""Publish the built distributions using uv."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

import typer
from plumbum import local

try:
    from syspath_hack import (
        SysPathMode,
        ensure_module_dir,
        prepend_project_root,
        prepend_to_syspath,
    )
except ImportError:  # pragma: no cover - compat for older syspath-hack
    import enum

    from syspath_hack import prepend_to_syspath

    class SysPathMode(enum.StrEnum):
        """Compatibility enum when syspath_hack lacks SysPathMode."""

        PREPEND = "prepend"
        APPEND = "append"

    def _prepend_path_to_syspath(path_str: str) -> None:
        """Place ``path_str`` at the start of sys.path, removing duplicates first."""
        if path_str in sys.path:
            sys.path.remove(path_str)
        sys.path.insert(0, path_str)

    def _append_path_to_syspath(path_str: str) -> None:
        """Append ``path_str`` to sys.path if it is not already present."""
        if path_str not in sys.path:
            sys.path.append(path_str)

    def ensure_module_dir(
        file: str | Path, *, mode: SysPathMode = SysPathMode.PREPEND
    ) -> Path:
        """Add the directory for *file* to sys.path in the requested mode."""
        path = Path(file).resolve().parent
        path_str = str(path)
        if mode == SysPathMode.PREPEND:
            _prepend_path_to_syspath(path_str)
        else:
            _append_path_to_syspath(path_str)
        return path

    def prepend_project_root(
        sigil: str = "pyproject.toml", *, extra_paths: list[Path] | None = None
    ) -> Path:
        """Ensure the project root marked by *sigil* is first on sys.path."""
        from syspath_hack import add_project_root, find_project_root

        root = find_project_root(sigil)
        add_project_root(sigil)
        for extra in extra_paths or []:
            prepend_to_syspath(extra)
        return root


from cmd_utils_importer import import_cmd_utils


def _ensure_python_runtime() -> None:
    """Fail fast when Python 3.13+ or uv provisioning is unavailable."""
    if sys.version_info >= (3, 13):
        return
    if shutil.which("uv") is not None:
        return
    typer.echo(
        "::error::Python >= 3.13 or uv must be available before publishing.",
        err=True,
    )
    raise typer.Exit(1)


def _extend_sys_path() -> None:
    candidates: list[Path] = []
    if action_path_env := os.getenv("GITHUB_ACTION_PATH"):
        action_path = Path(action_path_env).resolve()
        candidates.append(action_path / "scripts")
        with contextlib.suppress(IndexError):
            candidates.append(action_path.parents[2])
    else:
        scripts_dir = ensure_module_dir(__file__, mode=SysPathMode.PREPEND)
        candidates.append(scripts_dir)
        with contextlib.suppress(IndexError):
            candidates.append(scripts_dir.parents[3])

    for candidate in candidates:
        if not candidate.exists():
            continue
        prepend_to_syspath(candidate)


_ensure_python_runtime()
_extend_sys_path()

run_cmd = import_cmd_utils().run_cmd

INDEX_OPTION = typer.Option(
    "",
    envvar="INPUT_UV_INDEX",
    help="Optional index name or URL for uv publish.",
)


def main(index: str = "") -> None:
    """Publish the built distributions with uv.

    Parameters
    ----------
    index : str
        Optional package index name or URL to pass to ``uv publish``.
    """
    if index := index.strip():
        typer.echo(f"Publishing with uv to index '{index}'")
        run_cmd(local["uv"]["publish"]["--index", index])
    else:
        typer.echo("Publishing with uv to default index (PyPI)")
        run_cmd(local["uv"]["publish"])


def cli(index: str = INDEX_OPTION) -> None:
    """CLI entrypoint."""
    main(index=index)


if __name__ == "__main__":
    typer.run(cli)
