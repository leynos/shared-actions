#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
"""Helper utilities for composite action setup steps."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Import-time environment bootstrap.
# This script must configure sys.path and GITHUB_ACTION_PATH before importing
# cmd_utils_importer and toolchain modules to ensure they can be resolved when
# running outside GitHub Actions (e.g., via ``uv run --script``).
action_path = Path(__file__).resolve().parents[1]
if not action_path.exists():
    message = f"Action path does not exist: {action_path}"
    raise FileNotFoundError(message)
os.environ.setdefault("GITHUB_ACTION_PATH", str(action_path))

repo_root = Path(__file__).resolve().parents[4]
if not repo_root.exists():
    message = f"Repository root does not exist: {repo_root}"
    raise FileNotFoundError(message)
sys.path.insert(0, str(repo_root))

from cmd_utils_importer import ensure_cmd_utils_imported  # noqa: E402

ensure_cmd_utils_imported()

import typer  # noqa: E402
from toolchain import read_default_toolchain  # noqa: E402

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
