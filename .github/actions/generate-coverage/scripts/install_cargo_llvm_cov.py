#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Install the cargo-llvm-cov tool via ``cargo install``."""

import importlib.util
from pathlib import Path

import typer
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError

CMD_UTILS_FILENAME = "cmd_utils.py"
ERROR_REPO_ROOT_NOT_FOUND = "Repository root not found"
ERROR_IMPORT_FAILED = "Failed to import cmd_utils from repository root"


class RepoRootNotFoundError(RuntimeError):
    """Repository root not found."""

    def __init__(self, searched: str) -> None:
        super().__init__(f"{ERROR_REPO_ROOT_NOT_FOUND}; searched: {searched}")


class CmdUtilsImportError(RuntimeError):
    """Failed to import cmd_utils from repository root."""

    def __init__(self, path: Path, symbol: str | None = None) -> None:
        detail = f"'{symbol}' not found in {path}" if symbol is not None else str(path)
        super().__init__(f"{ERROR_IMPORT_FAILED}: {detail}")


def _find_repo_root() -> Path:
    """Locate the repository root containing CMD_UTILS_FILENAME."""
    parents = list(Path(__file__).resolve().parents)
    for parent in parents:
        if (parent / CMD_UTILS_FILENAME).exists():
            return parent
    searched = " -> ".join(str(p) for p in parents)
    raise RepoRootNotFoundError(searched)


REPO_ROOT = _find_repo_root()
spec = importlib.util.spec_from_file_location(
    "cmd_utils", REPO_ROOT / CMD_UTILS_FILENAME
)
if spec is None or spec.loader is None:  # pragma: no cover - import-time failure
    raise CmdUtilsImportError(REPO_ROOT)
cmd_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmd_utils)
try:
    run_cmd = cmd_utils.run_cmd
except AttributeError as exc:  # pragma: no cover - import-time failure
    missing = REPO_ROOT / CMD_UTILS_FILENAME
    raise CmdUtilsImportError(missing, "run_cmd") from exc


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
