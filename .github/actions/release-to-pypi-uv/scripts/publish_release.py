#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["plumbum", "syspath-hack>=0.3.0,<0.4.0", "typer>=0.17,<0.18"]
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
from syspath_hack import (
    SysPathMode,
    ensure_module_dir,
    prepend_to_syspath,
)

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
