#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.17,<0.18"]
# ///
"""Publish the built distributions using uv."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

import typer


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
    action_path_env = os.getenv("GITHUB_ACTION_PATH")
    if action_path_env:
        action_path = Path(action_path_env).resolve()
        candidates.append(action_path / "scripts")
        with contextlib.suppress(IndexError):
            candidates.append(action_path.parents[2])
    else:
        script_path = Path(__file__).resolve()
        scripts_dir = script_path.parent
        candidates.append(scripts_dir)
        with contextlib.suppress(IndexError):
            candidates.append(scripts_dir.parents[3])

    for candidate in candidates:
        if not candidate.exists():
            continue
        path_str = str(candidate)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


_ensure_python_runtime()
_extend_sys_path()

from cmd_utils import run_cmd  # noqa: E402

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
        run_cmd(["uv", "publish", "--index", index])
    else:
        typer.echo("Publishing with uv to default index (PyPI)")
        run_cmd(["uv", "publish"])


def cli(index: str = INDEX_OPTION) -> None:
    """CLI entrypoint."""
    main(index=index)


if __name__ == "__main__":
    typer.run(cli)
