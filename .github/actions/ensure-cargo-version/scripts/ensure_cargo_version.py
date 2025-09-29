#!/usr/bin/env -S uv run python
# fmt: off
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
# ]
# ///
# fmt: on

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Iterable

import cyclopts
from cyclopts import App, Parameter

app = App(config=cyclopts.config.Env("INPUT_", command=False))


@dataclass(slots=True)
class ManifestVersion:
    path: Path
    version: str


class ManifestError(Exception):
    """Raised when a manifest cannot be processed."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def _workspace() -> Path:
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        return Path(workspace)
    return Path.cwd()


def _resolve_paths(manifests: Iterable[Path]) -> list[Path]:
    workspace = _workspace()
    resolved: list[Path] = []
    for manifest in manifests:
        path = manifest if manifest.is_absolute() else workspace / manifest
        resolved.append(path)
    return resolved


def _read_manifest_version(path: Path) -> ManifestVersion:
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
    print(f"::error title={title}::{message}")


def _tag_from_ref() -> str:
    ref_name = os.environ.get("GITHUB_REF_NAME")
    if not ref_name:
        raise RuntimeError("GITHUB_REF_NAME is not set")
    return ref_name[1:] if ref_name.startswith("v") else ref_name


def _write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _display_path(path: Path) -> str:
    workspace = _workspace()
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


@app.default
def main(
    *,
    manifests: Annotated[list[Path] | None, Parameter()] = None,
) -> None:
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

    for manifest_version in manifest_versions:
        if manifest_version.version != tag_version:
            errors.append(
                (
                    "Tag/Cargo.toml mismatch",
                    "Tag version {} does not match Cargo.toml version {} for {}".format(
                        tag_version,
                        manifest_version.version,
                        _display_path(manifest_version.path),
                    ),
                )
            )

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
