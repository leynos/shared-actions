#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install the cargo-llvm-cov tool via ``cargo install``."""

import sys
from pathlib import Path

import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

ERROR_REPO_ROOT_NOT_FOUND = "Could not find repository root containing cmd_utils.py"
ERROR_IMPORT_FAILED = "Failed to import cmd_utils from repository root"


def _find_repo_root() -> Path:
    """Locate the repository root containing ``cmd_utils.py``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "cmd_utils.py").exists():
            return parent
    raise RuntimeError(ERROR_REPO_ROOT_NOT_FOUND)


REPO_ROOT = _find_repo_root()
sys.path.insert(0, str(REPO_ROOT))
try:
    from cmd_utils import run_cmd  # noqa: E402,RUF100
except ModuleNotFoundError as exc:  # pragma: no cover - import-time failure
    raise RuntimeError(ERROR_IMPORT_FAILED) from exc


def main() -> None:
    """Install cargo-llvm-cov via cargo install command."""
    try:
        cmd = cargo["install", "cargo-llvm-cov", "--force"]
        run_cmd(cmd)
        typer.echo("cargo-llvm-cov installed successfully")
    except ProcessExecutionError as exc:
        typer.echo(
            f"cargo install failed with code {exc.retcode}: {exc.stderr}", err=True
        )
        raise typer.Exit(code=exc.retcode or 1) from exc


if __name__ == "__main__":
    typer.run(main)
