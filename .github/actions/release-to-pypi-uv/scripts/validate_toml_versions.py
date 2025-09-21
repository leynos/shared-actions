#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer"]
# ///
"""Validate that project versions in pyproject.toml files match the release version."""

from __future__ import annotations

import glob
import typing as typ
from pathlib import Path

import typer

VERSION_OPTION = typer.Option(..., envvar="RESOLVED_VERSION")
PATTERN_OPTION = typer.Option("**/pyproject.toml", envvar="INPUT_TOML_GLOB")
FAIL_ON_DYNAMIC_OPTION = typer.Option(
    "false",
    envvar="INPUT_FAIL_ON_DYNAMIC_VERSION",
)

SKIP_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".direnv",
    ".mypy_cache",
}

TRUTHY_STRINGS = {"true", "1", "yes", "y", "on"}


def _iter_files(pattern: str) -> typ.Iterable[Path]:
    candidates = [Path(p) for p in glob.glob(pattern, recursive=True)]
    for path in candidates:
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & SKIP_PARTS:
            continue
        yield path


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return normalized in TRUTHY_STRINGS


def _load_toml(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"{path}: failed to read: {exc}"
        raise RuntimeError(message) from exc

    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - python < 3.11
        message = "tomllib module is unavailable"
        raise RuntimeError(message) from exc

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:  # type: ignore[attr-defined]
        message = f"{path}: failed to parse: {exc}"
        raise RuntimeError(message) from exc


def main(
    version: str = VERSION_OPTION,
    pattern: str = PATTERN_OPTION,
    fail_on_dynamic: str = FAIL_ON_DYNAMIC_OPTION,
) -> None:
    """Confirm that project versions in TOML files match the release version.

    Parameters
    ----------
    version : str
        Semantic version resolved for the release tag.
    pattern : str
        Glob pattern used to discover ``pyproject.toml`` files to inspect.
    fail_on_dynamic : str
        String flag that controls whether dynamic versions should raise an
        error.

    Raises
    ------
    typer.Exit
        Raised when TOML files cannot be read or contain mismatched versions.
    """
    files = list(_iter_files(pattern))
    if not files:
        typer.echo(f"::warning::No TOML files matched pattern {pattern}")
        return

    literal_version_errors: list[str] = []
    dynamic_errors: list[str] = []
    checked = 0
    fail_dynamic = _parse_bool(fail_on_dynamic)

    for path in files:
        try:
            data = _load_toml(path)
        except RuntimeError as exc:
            typer.echo(f"::error::{exc}", err=True)
            raise typer.Exit(1) from exc

        project = data.get("project")
        if not isinstance(project, dict):
            continue
        checked += 1

        dynamic = project.get("dynamic")
        dynamic_set = (
            {str(item) for item in dynamic}
            if isinstance(dynamic, (list, tuple))
            else set()
        )
        if "version" in dynamic_set:
            message = f"{path}: uses dynamic 'version' (PEP 621)."
            if fail_dynamic:
                dynamic_errors.append(
                    f"{message} Set fail-on-dynamic-version=false to allow."
                )
            else:
                typer.echo(f"::notice::{message} Skipping version check.")
            continue

        toml_version = project.get("version")
        if toml_version is None:
            literal_version_errors.append(
                f"{path}: missing [project].version and not marked dynamic"
            )
            continue

        if str(toml_version) != version:
            literal_version_errors.append(
                f"{path}: [project].version '{toml_version}' != tag version '{version}'"
            )

    if dynamic_errors or literal_version_errors:
        for error in (*dynamic_errors, *literal_version_errors):
            typer.echo(f"::error::{error}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"Checked {checked} PEP 621 project file(s); all versions match {version}."
    )


if __name__ == "__main__":
    typer.run(main)
