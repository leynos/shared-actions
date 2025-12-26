#!/usr/bin/env -S uv run --script
# fmt: off
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
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
import sys
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

# Add repository root to path for cargo_utils import
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cargo_utils import (
    ManifestError,
    get_bin_name,
    get_package_field,
    read_manifest,
    resolve_version,
)

app = App(config=cyclopts.config.Env("INPUT_", command=False))


def _coerce_bool(*, value: bool | str, parameter: str) -> bool:
    """Convert boolean-like values into ``bool`` instances."""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"1", "true", "yes", "on"}:
            return True
        if normalised in {"0", "false", "no", "off", ""}:
            return False

    message = (
        f"Invalid value for {parameter!s}: {value!r}. Expected a boolean-like string."
    )
    raise ValueError(message)


def _write_output(name: str, value: str) -> None:
    """Append an output variable for downstream steps."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _write_env(name: str, value: str) -> None:
    """Append an environment variable for subsequent steps."""
    env_path = os.environ.get("GITHUB_ENV")
    if not env_path:
        return
    path = Path(env_path)
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


def _extract_field(
    manifest: dict[str, typ.Any],
    manifest_path: Path,
    field: str,
) -> str | None:
    """Extract a single field from the manifest."""
    if field == "name":
        return get_package_field(manifest, "name")
    if field == "version":
        return resolve_version(manifest, manifest_path)
    if field == "bin-name":
        return get_bin_name(manifest)
    if field == "description":
        package = manifest.get("package", {})
        desc = package.get("description")
        return desc.strip() if isinstance(desc, str) else None
    return None


@app.default
def main(
    *,
    manifest_path: typ.Annotated[str, Parameter()] = "Cargo.toml",
    fields: typ.Annotated[str, Parameter()] = "name,version",
    export_to_env: typ.Annotated[bool | str, Parameter()] = True,
) -> None:
    """Extract and export Cargo manifest metadata."""
    resolved_path = Path(manifest_path)
    if not resolved_path.is_absolute():
        workspace = os.environ.get("GITHUB_WORKSPACE", "")
        if workspace:
            resolved_path = Path(workspace) / resolved_path

    try:
        should_export_env = _coerce_bool(value=export_to_env, parameter="export-to-env")
    except ValueError as exc:
        _emit_error("Invalid input", str(exc))
        raise SystemExit(1) from exc

    try:
        manifest = read_manifest(resolved_path)
    except ManifestError as exc:
        _emit_error("Cargo.toml read failure", str(exc), path=exc.path)
        raise SystemExit(1) from exc

    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    exported: list[str] = []

    for field in field_list:
        try:
            value = _extract_field(manifest, resolved_path, field)
        except ManifestError as exc:
            _emit_error("Field extraction failed", str(exc), path=exc.path)
            raise SystemExit(1) from exc

        if value is None:
            continue

        output_name = field.replace("_", "-")
        _write_output(output_name, value)
        exported.append(f"{output_name}={value}")

        if should_export_env:
            env_name = field.upper().replace("-", "_")
            _write_env(env_name, value)

    if exported:
        print(f"Exported: {', '.join(exported)}")
    else:
        print("No fields exported")


if __name__ == "__main__":
    app()
