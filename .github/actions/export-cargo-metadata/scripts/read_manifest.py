#!/usr/bin/env -S uv run --script
# fmt: off
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
# ]
# ///
# fmt: on

"""Export Cargo.toml metadata as GitHub Actions outputs.

Extract package metadata fields from a Cargo manifest and emit them as
GitHub Actions outputs. Optionally exports fields to GITHUB_ENV for use
in subsequent workflow steps.

Examples
--------
Run with default inputs::

    INPUT_MANIFEST_PATH=Cargo.toml INPUT_FIELDS=name,version uv run read_manifest.py

Extract specific fields::

    INPUT_FIELDS=name,version,bin-name uv run read_manifest.py
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from syspath_hack import prepend_project_root

# Add repository root to path for cargo_utils and bool_utils imports
prepend_project_root()

from bool_utils import coerce_bool_strict
from cargo_utils import (
    ManifestError,
    get_bin_name,
    get_package_field,
    read_manifest,
    resolve_version,
)

app = App(config=cyclopts.config.Env("INPUT_", command=False))


def _write_output(name: str, value: str) -> None:
    """Append an output variable for downstream steps."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _write_env(name: str, value: str) -> None:
    """Append an environment variable for subsequent steps."""
    env_path = os.environ.get("GITHUB_ENV")
    if not env_path:
        return
    path = Path(env_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _emit_error(title: str, message: str, *, path: Path | None = None) -> None:
    """Print an error in the format expected by GitHub Actions."""

    def _esc(value: str) -> str:
        return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    metadata_parts: list[str] = []
    if path is not None:
        metadata_parts.append(f"file={_esc(str(path))}")
        metadata_parts.append("line=1")
    metadata_parts.append(f"title={_esc(title)}")
    metadata = ",".join(metadata_parts)
    print(f"::error {metadata}::{_esc(message)}")


_SUPPORTED_FIELDS: frozenset[str] = frozenset(
    {"name", "version", "bin-name", "description"}
)


def _emit_warning(message: str) -> None:
    """Print a warning in the format expected by GitHub Actions."""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{escaped}")


def _extract_field(
    manifest: dict[str, typ.Any],
    manifest_path: Path,
    field: str,
) -> str | None:
    """Extract a single field from the manifest."""
    match field:
        case "name":
            return get_package_field(manifest, "name", manifest_path)
        case "version":
            return resolve_version(manifest, manifest_path)
        case "bin-name":
            return get_bin_name(manifest, manifest_path)
        case "description":
            package = manifest.get("package")
            if not isinstance(package, dict):
                return None
            desc = package.get("description")
            return desc.strip() if isinstance(desc, str) else None
        case _:
            return None


def _resolve_manifest_path(manifest_path: str) -> Path:
    """Resolve the manifest path, considering GITHUB_WORKSPACE."""
    resolved_path = Path(manifest_path)
    if resolved_path.is_absolute():
        return resolved_path
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    if workspace:
        return Path(workspace) / resolved_path
    return resolved_path


def _load_manifest(resolved_path: Path) -> dict[str, typ.Any]:
    """Load the manifest with error handling."""
    try:
        return read_manifest(resolved_path)
    except ManifestError as exc:
        _emit_error("Cargo.toml read failure", str(exc), path=exc.path)
        raise SystemExit(1) from exc


def _process_fields(
    manifest: dict[str, typ.Any],
    manifest_path: Path,
    field_list: list[str],
    *,
    should_export_env: bool,
) -> list[str]:
    """Process all fields and return the list of exported values."""
    exported: list[str] = []

    for field in field_list:
        if field not in _SUPPORTED_FIELDS:
            supported = ", ".join(sorted(_SUPPORTED_FIELDS))
            _emit_warning(f"Unknown field '{field}' ignored. Supported: {supported}")
            continue

        try:
            value = _extract_field(manifest, manifest_path, field)
        except ManifestError as exc:
            _emit_error("Field extraction failed", str(exc), path=exc.path)
            raise SystemExit(1) from exc

        if value is None:
            continue

        output_name = field.replace("_", "-")
        _write_output(output_name, value)

        if should_export_env:
            env_name = field.upper().replace("-", "_")
            _write_env(env_name, value)

        exported.append(f"{output_name}={value}")

    return exported


@app.default
def main(
    *,
    manifest_path: typ.Annotated[str, Parameter()] = "Cargo.toml",
    fields: typ.Annotated[str, Parameter()] = "name,version",
    export_to_env: typ.Annotated[bool | str, Parameter()] = True,
) -> None:
    """Extract and export Cargo manifest metadata."""
    resolved_path = _resolve_manifest_path(manifest_path)

    try:
        should_export_env = coerce_bool_strict(export_to_env, parameter="export-to-env")
    except ValueError as exc:
        _emit_error("Invalid input", str(exc))
        raise SystemExit(1) from exc

    manifest = _load_manifest(resolved_path)

    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    exported = _process_fields(
        manifest, resolved_path, field_list, should_export_env=should_export_env
    )

    if exported:
        print(f"Exported: {', '.join(exported)}")
    else:
        print("No fields exported")


if __name__ == "__main__":
    app()
