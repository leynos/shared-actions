#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "syspath-hack>=0.4.0,<0.5.0", "typer"]
# ///
"""Helper utilities for composite action setup steps."""

from __future__ import annotations

import os
import re
from pathlib import Path

from syspath_hack import find_project_root, prepend_to_syspath

# The bootstrap walks upward from this module's parent directory to locate key
# directories instead of relying on hard-coded parent counts.
_BOOTSTRAP_CACHE: tuple[Path, Path] | None = None


def _initialise_cmd_utils() -> None:
    """Load ``cmd_utils`` helpers for downstream imports."""
    try:
        from cmd_utils_importer import ensure_cmd_utils_imported

        ensure_cmd_utils_imported()
    except ImportError as exc:  # pragma: no cover - defensive guard
        message = (
            "Failed to import cmd_utils_importer. Ensure the repository "
            "structure is intact and sys.path is configured correctly."
        )
        raise ImportError(message) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        message = (
            "Failed to initialise cmd_utils. Check that the environment is "
            "configured correctly for this script to run."
        )
        raise RuntimeError(message) from exc


def bootstrap_environment() -> tuple[Path, Path]:
    """Ensure imports succeed when the script runs outside GitHub Actions."""
    global _BOOTSTRAP_CACHE
    if _BOOTSTRAP_CACHE is not None:
        return _BOOTSTRAP_CACHE

    script_dir = Path(__file__).resolve().parent
    action_path = find_project_root(sigil="action.yml", start=script_dir)
    os.environ.setdefault("GITHUB_ACTION_PATH", str(action_path))

    repo_root = find_project_root(start=script_dir)
    prepend_to_syspath(repo_root)

    _initialise_cmd_utils()

    _BOOTSTRAP_CACHE = (action_path, repo_root)
    return _BOOTSTRAP_CACHE


_ACTION_PATH, _REPO_ROOT = bootstrap_environment()

import typer
from toolchain import read_default_toolchain

TARGET_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

app = typer.Typer(add_completion=False)


class TargetValidationError(ValueError):
    """Raised when a provided target triple is invalid."""


class ToolchainResolutionError(ValueError):
    """Raised when the action cannot resolve a toolchain."""


def validate_target(target: str) -> None:
    """Validate *target* and raise :class:`TargetValidationError` on failure."""
    if not target:
        message = "target input must not be empty"
        raise TargetValidationError(message)
    if not TARGET_PATTERN.match(target):
        message = f"target '{target}' contains invalid characters"
        raise TargetValidationError(message)
    parts = target.split("-")
    if len(parts) < 2:
        message = f"target '{target}' must contain at least two '-' separated segments"
        raise TargetValidationError(message)


def resolve_toolchain(
    default_toolchain: str, target: str, runner_os: str, runner_arch: str
) -> str:
    """Return the toolchain identifier for the provided runner metadata."""
    if runner_os == "Windows" and target.endswith("-pc-windows-gnu"):
        arch_map = {"X64": "x86_64", "ARM64": "aarch64"}
        try:
            host_arch = arch_map[runner_arch]
        except KeyError as exc:  # pragma: no cover - defensive guard
            message = (
                "unrecognized runner architecture "
                f"'{runner_arch}' for Windows toolchain"
            )
            raise ToolchainResolutionError(message) from exc
        return f"{default_toolchain}-{host_arch}-pc-windows-gnu"
    return default_toolchain


@app.command()
def validate(target: str = typer.Argument(...)) -> None:
    """CLI entry point for validating targets."""
    try:
        validate_target(target)
    except TargetValidationError as exc:
        typer.echo(f"::error:: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def toolchain(
    target: str = typer.Option(..., "--target"),
    runner_os: str = typer.Option(..., "--runner-os"),
    runner_arch: str = typer.Option(..., "--runner-arch"),
) -> None:
    """CLI entry point that prints the resolved toolchain."""
    default_toolchain = read_default_toolchain()
    try:
        resolved = resolve_toolchain(default_toolchain, target, runner_os, runner_arch)
    except ToolchainResolutionError as exc:
        typer.echo(f"::error:: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(resolved)


if __name__ == "__main__":
    app()
