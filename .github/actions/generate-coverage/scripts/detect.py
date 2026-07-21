#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer"]
# ///
"""Detect the project language and validate coverage format compatibility."""

from __future__ import annotations

import enum
import os
import tomllib
import typing as typ
from pathlib import Path

import typer


class CoverageFmt(enum.StrEnum):
    """Supported coverage report formats."""

    LCOV = "lcov"
    COBERTURA = "cobertura"
    COVERAGEPY = "coveragepy"


class Lang(enum.StrEnum):
    """Project languages supported by the action."""

    RUST = "rust"
    PYTHON = "python"
    MIXED = "mixed"


class LangMode(enum.StrEnum):
    """Requested coverage language mode from the ``language`` input.

    ``AUTO`` preserves manifest-based detection; the remaining values force the
    requested coverage scope and reject repositories that lack the matching
    prerequisites.
    """

    AUTO = "auto"
    RUST = "rust"
    PYTHON = "python"
    MIXED = "mixed"


FMT_OPT = typer.Option(
    CoverageFmt.COBERTURA.value,
    envvar="INPUT_FORMAT",
    help="Coverage format: lcov, cobertura, or coveragepy",
)
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")


def _resolve_cargo_manifest(cargo_manifest: str) -> Path | None:
    """Resolve the selected Cargo manifest with root precedence."""
    root_manifest = Path("Cargo.toml")
    if root_manifest.is_file():
        return root_manifest

    configured_manifest = cargo_manifest.strip()
    if not configured_manifest:
        return None

    configured_path = Path(configured_manifest)
    if configured_path.is_file():
        return configured_path

    return None


def _has_python_project() -> bool:
    """Return whether the repository root holds a uv-syncable Python project.

    This mirrors the ``uv sync`` contract that :mod:`run_python` depends on:
    the coverage run installs project dependencies with
    ``uv sync --inexact --python``, which only succeeds when
    ``pyproject.toml`` declares a project that uv is allowed to manage. A
    configuration-only ``pyproject.toml`` used solely for tooling (for
    example Ruff, Pylint, ty) declares no ``[project]`` table and typically
    sets ``[tool.uv] managed = false``; such a file is not a Python coverage
    project and must not force a Python or mixed coverage run.
    """
    manifest = Path("pyproject.toml")
    if not manifest.is_file():
        return False
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool_uv = data.get("tool", {}).get("uv", {})
    if tool_uv.get("managed") is False:
        return False
    return "project" in data or "workspace" in tool_uv


def _auto_lang(selected_manifest: Path | None) -> tuple[Lang, Path | None]:
    """Detect the language from present manifests (historical behaviour)."""
    python = Path("pyproject.toml").is_file()
    if selected_manifest is not None:
        return (Lang.MIXED if python else Lang.RUST), selected_manifest
    if python:
        return Lang.PYTHON, None
    typer.echo("Neither Cargo.toml nor pyproject.toml found", err=True)
    raise typer.Exit(code=1)


def _fail(message: str) -> typ.NoReturn:
    """Emit ``message`` on stderr and exit with code 1."""
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _forced_rust(selected_manifest: Path | None) -> tuple[Lang, Path | None]:
    """Resolve ``language=rust``; a configuration-only pyproject is ignored."""
    if selected_manifest is None:
        _fail("language=rust requires a Cargo manifest, but none was found")
    return Lang.RUST, selected_manifest


def _forced_python(selected_manifest: Path | None) -> tuple[Lang, Path | None]:
    """Resolve ``language=python``; requires a syncable ``[project]`` table."""
    if not _has_python_project():
        _fail(
            "language=python requires a syncable pyproject.toml with a "
            "[project] table, but none was found"
        )
    return Lang.PYTHON, None


def _forced_mixed(selected_manifest: Path | None) -> tuple[Lang, Path | None]:
    """Resolve ``language=mixed``; requires both Rust and Python prerequisites."""
    missing = [
        need
        for ok, need in (
            (selected_manifest is not None, "a Cargo manifest"),
            (_has_python_project(), "a syncable pyproject.toml with a [project] table"),
        )
        if not ok
    ]
    if missing:
        _fail(
            f"language=mixed requires {' and '.join(missing)}, but not all "
            "prerequisites were found"
        )
    return Lang.MIXED, selected_manifest


# Explicit (non-``auto``) language modes dispatch to a single-responsibility
# resolver that validates the mode's prerequisites.
_FORCED_RESOLVERS: dict[
    LangMode, typ.Callable[[Path | None], tuple[Lang, Path | None]]
] = {
    LangMode.RUST: _forced_rust,
    LangMode.PYTHON: _forced_python,
    LangMode.MIXED: _forced_mixed,
}


def get_lang(
    cargo_manifest: str = "", mode: LangMode = LangMode.AUTO
) -> tuple[Lang, Path | None]:
    """Detect project language and selected Cargo manifest (if any)."""
    selected_manifest = _resolve_cargo_manifest(cargo_manifest)
    if mode is LangMode.AUTO:
        return _auto_lang(selected_manifest)
    return _FORCED_RESOLVERS[mode](selected_manifest)


def _parse_lang_mode(raw: str) -> LangMode:
    """Parse the requested language mode, rejecting unsupported values."""
    try:
        return LangMode(raw.strip().lower())
    except ValueError as exc:
        valid = ", ".join(item.value for item in LangMode)
        typer.echo(f"Unsupported language: {raw} (expected one of: {valid})", err=True)
        raise typer.Exit(code=1) from exc


def _parse_format(fmt: str) -> CoverageFmt:
    """Parse the coverage format, rejecting unsupported values."""
    try:
        return CoverageFmt(fmt.lower())
    except ValueError as exc:
        typer.echo(f"Unsupported format: {fmt}", err=True)
        raise typer.Exit(code=1) from exc


def _resolve_detect_inputs(cargo_manifest: str, language: str) -> tuple[str, str]:
    """Fill unset detector inputs from the environment."""
    cargo_manifest = cargo_manifest or os.getenv("INPUT_CARGO_MANIFEST", "")
    language = language or os.getenv("INPUT_LANGUAGE", "") or LangMode.AUTO.value
    return cargo_manifest, language


def _check_format_compatibility(lang: Lang, fmt: CoverageFmt) -> None:
    """Reject language/format combinations the action cannot produce."""
    match (lang, fmt):
        case (Lang.RUST, CoverageFmt.COVERAGEPY):
            _fail("coveragepy format only supported for Python projects")
        case (Lang.PYTHON, CoverageFmt.LCOV):
            _fail("lcov format only supported for Rust projects")
        case (Lang.MIXED, other) if other != CoverageFmt.COBERTURA:
            _fail("Mixed projects only support cobertura format")


def main(
    fmt: str = FMT_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
    cargo_manifest: str = "",
    language: str = "",
) -> None:
    """Detect the project language and write it plus the format to ``GITHUB_OUTPUT``."""
    cargo_manifest, language = _resolve_detect_inputs(cargo_manifest, language)
    mode = _parse_lang_mode(language)
    lang, selected_manifest = get_lang(cargo_manifest, mode)
    fmt_enum = _parse_format(fmt)
    _check_format_compatibility(lang, fmt_enum)

    with github_output.open("a") as fh:
        fh.write(f"lang={lang.value}\nfmt={fmt_enum.value}\n")
        if selected_manifest is not None:
            fh.write(f"cargo_manifest={selected_manifest}\n")


if __name__ == "__main__":
    typer.run(main)
