#!/usr/bin/env -S uv run python
# fmt: off
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
# ]
# ///
# fmt: on

"""Ensure release tags match Cargo manifest versions."""

from __future__ import annotations

import dataclasses
import os
import tomllib
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

app = App(config=cyclopts.config.Env("INPUT_", command=False))


@dataclasses.dataclass(slots=True)
class ManifestVersion:
    """Version metadata extracted from a manifest."""

    path: Path
    version: str


class ManifestError(Exception):
    """Raised when a manifest cannot be processed."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def _workspace() -> Path:
    """Return the workspace root supplied by GitHub Actions."""
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        return Path(workspace)
    return Path.cwd()


def _resolve_paths(manifests: list[Path]) -> list[Path]:
    """Resolve manifest paths relative to the workspace."""
    workspace = _workspace()
    resolved: list[Path] = []
    for manifest in manifests:
        path = manifest if manifest.is_absolute() else workspace / manifest
        resolved.append(path)
    return resolved


def _read_manifest_version(path: Path) -> ManifestVersion:
    """Parse a manifest and return the discovered package version."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:  # pragma: no cover - fatal path
        raise ManifestError(path, "Manifest not found") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(path, "Invalid TOML in manifest") from exc

    package = data.get("package")
    if not isinstance(package, dict):
        raise ManifestError(path, "Manifest missing [package] table")

    version = package.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestError(path, "Could not read package.version")

    return ManifestVersion(path=path, version=version.strip())


def _emit_error(title: str, message: str) -> None:
    """Print an error in the format expected by GitHub Actions."""
    print(f"::error title={title}::{message}")


def _tag_from_ref() -> str:
    """Return the tag version from the GitHub reference name."""
    ref_name = os.environ.get("GITHUB_REF_NAME")
    if not ref_name:
        message = "GITHUB_REF_NAME is not set"
        raise RuntimeError(message)
    return ref_name.removeprefix("v")


def _write_output(name: str, value: str) -> None:
    """Append an output variable for downstream steps."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _display_path(path: Path) -> str:
    """Return a display-friendly version of a manifest path."""
    workspace = _workspace()
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


@app.default
def main(
    *,
    manifests: typ.Annotated[list[Path] | None, Parameter()] = None,
) -> None:
    """Validate that each manifest matches the tag-derived version."""
    manifest_args = manifests if manifests else [Path("Cargo.toml")]
    resolved = _resolve_paths(manifest_args)

    try:
        tag_version = _tag_from_ref()
    except RuntimeError as exc:
        _emit_error("Missing tag", str(exc))
        raise SystemExit(1) from exc

    manifest_versions: list[ManifestVersion] = []
    errors: list[tuple[str, str]] = []

    for manifest_path in resolved:
        try:
            manifest_versions.append(_read_manifest_version(manifest_path))
        except ManifestError as exc:
            errors.append(
                ("Cargo.toml parse failure", f"{exc} in {_display_path(exc.path)}")
            )

    mismatch_errors = [
        (
            "Tag/Cargo.toml mismatch",
            (
                f"Tag version {tag_version} does not match Cargo.toml version "
                f"{manifest_version.version}"
                f" for {_display_path(manifest_version.path)}"
            ),
        )
        for manifest_version in manifest_versions
        if manifest_version.version != tag_version
    ]
    errors.extend(mismatch_errors)

    if errors:
        for title, message in errors:
            _emit_error(title, message)
        raise SystemExit(1)

    manifest_list = ", ".join(_display_path(item.path) for item in manifest_versions)
    print(
        f"Release tag {tag_version} matches Cargo.toml version(s) in: {manifest_list}."
    )
    _write_output("version", tag_version)


if __name__ == "__main__":
    app()
