#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "syspath-hack>=0.4.0,<0.5.0",
# ]
# ///

"""Ensure release tags match Cargo manifest versions."""

from __future__ import annotations

import dataclasses
import os
import re
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from syspath_hack import prepend_project_root

_SCRIPT_DIR = Path(__file__).resolve().parent

# Add repository root to path for cargo_utils and bool_utils imports
prepend_project_root(start=_SCRIPT_DIR)

from bool_utils import coerce_bool_strict
from cargo_utils import (
    ManifestError,
    find_workspace_root,
    get_workspace_version,
    read_manifest,
)

app = App(config=cyclopts.config.Env("INPUT_", command=False))


@dataclasses.dataclass(slots=True)
class ManifestVersion:
    """Metadata extracted from a manifest."""

    path: Path
    name: str
    version: str


def _workspace() -> Path:
    """Return the workspace root supplied by GitHub Actions."""
    if workspace := os.environ.get("GITHUB_WORKSPACE"):
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
    """Parse a manifest and return the discovered package metadata."""
    data = read_manifest(path)

    package = data.get("package")
    if not isinstance(package, dict):
        raise ManifestError(path, "Manifest missing [package] table")

    name = package.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError(path, "Could not read package.name")
    crate_name = name.strip()

    version = package.get("version")
    if isinstance(version, dict) and version.get("workspace") is True:
        workspace_manifest = find_workspace_root(path.parent)
        if workspace_manifest is None:
            raise ManifestError(
                path,
                "Could not resolve workspace root for inherited version",
            )
        workspace_version = get_workspace_version(workspace_manifest)
        if workspace_version is None:
            raise ManifestError(
                workspace_manifest,
                "Workspace manifest missing [workspace.package].version",
            )
        return ManifestVersion(path=path, name=crate_name, version=workspace_version)

    if not isinstance(version, str) or not version.strip():
        raise ManifestError(path, "Could not read package.version")

    return ManifestVersion(path=path, name=crate_name, version=version.strip())


def _emit_error(title: str, message: str, *, path: Path | None = None) -> None:
    """Print an error in the format expected by GitHub Actions."""

    def _esc(value: str) -> str:
        return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    metadata_parts: list[str] = []
    if path is not None:
        metadata_parts.append(f"file={_esc(_display_path(path))}")
        metadata_parts.append("line=1")
    metadata_parts.append(f"title={_esc(title)}")
    metadata = ",".join(metadata_parts)
    print(f"::error {metadata}::{_esc(message)}")


def _tag_from_ref(prefix: str) -> str:
    """Extract a SemVer version from the GitHub reference name."""
    ref_name = os.environ.get("GITHUB_REF_NAME")
    if not ref_name:
        message = "GITHUB_REF_NAME is not set"
        raise RuntimeError(message)

    if prefix and ref_name.startswith(prefix):
        ref_name = ref_name[len(prefix) :]

    match = re.search(
        r"(?:^|[-_/])v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$",
        ref_name,
    )
    if match:
        return match.group(1)

    return ref_name


def _write_output(name: str, value: str) -> None:
    """Append an output variable for downstream steps."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    tag_prefix: typ.Annotated[str, Parameter()] = "v",
    check_tag: typ.Annotated[bool | str, Parameter()] = True,
) -> None:
    """Validate that each manifest matches the tag-derived version."""
    manifest_args = manifests if manifests else [Path("Cargo.toml")]
    resolved = _resolve_paths(manifest_args)

    try:
        should_check_tag = coerce_bool_strict(check_tag, parameter="check-tag")
    except ValueError as exc:
        _emit_error("Invalid input", str(exc))
        raise SystemExit(1) from exc

    tag_version: str | None = None
    if should_check_tag:
        try:
            tag_version = _tag_from_ref(tag_prefix)
        except RuntimeError as exc:
            _emit_error("Missing tag", str(exc))
            raise SystemExit(1) from exc
    else:
        # Best-effort: expose version if a ref is present, but do not fail if absent.
        if os.environ.get("GITHUB_REF_NAME"):
            tag_version = _tag_from_ref(tag_prefix)

    manifest_versions: list[ManifestVersion] = []
    errors: list[tuple[str, str, Path | None]] = []

    for manifest_path in resolved:
        try:
            manifest_versions.append(_read_manifest_version(manifest_path))
        except ManifestError as exc:
            errors.append(
                (
                    "Cargo.toml parse failure",
                    f"{exc} in {_display_path(exc.path)}",
                    exc.path,
                )
            )

    crate_version = manifest_versions[0].version if manifest_versions else ""
    crate_name = manifest_versions[0].name if manifest_versions else ""

    if should_check_tag:
        if tag_version is None:  # pragma: no cover - defensive guard
            message = "Tag comparison requested but no tag version was derived."
            raise RuntimeError(message)
        expected_tag = tag_version
        mismatch_errors = [
            (
                "Tag/Cargo.toml mismatch",
                (
                    f"Tag version {expected_tag} does not match Cargo.toml version "
                    f"{manifest_version.version}"
                    f" for {_display_path(manifest_version.path)}"
                ),
                manifest_version.path,
            )
            for manifest_version in manifest_versions
            if manifest_version.version != expected_tag
        ]
        errors.extend(mismatch_errors)

    if errors:
        for title, message, path in errors:
            _emit_error(title, message, path=path)
        raise SystemExit(1)

    _write_output("crate-version", crate_version)
    _write_output("crate-name", crate_name)
    manifest_list = ", ".join(_display_path(item.path) for item in manifest_versions)
    if should_check_tag:
        print(
            "Release tag "
            f"{expected_tag} matches Cargo.toml version(s) in: {manifest_list}."
        )
    else:
        print(
            "Cargo.toml version(s) located in: "
            f"{manifest_list}. Tag comparison disabled by input."
        )
    if tag_version is not None:
        _write_output("version", tag_version)


if __name__ == "__main__":
    app()
