r"""Utilities for reading Cargo.toml manifest files.

This module provides functions for parsing Cargo manifest files and
extracting package metadata such as name, version, and binary targets.
It supports workspace inheritance and handles missing or malformed
manifests gracefully.

Examples
--------
Basic usage to read package name and version::

    >>> from pathlib import Path
    >>> manifest_path = Path("Cargo.toml")
    >>> manifest = read_manifest(manifest_path)
    >>> get_package_field(manifest, "name", manifest_path)
    'my-package'
    >>> get_package_field(manifest, "version", manifest_path)
    '1.2.3'

Extracting the binary name with [[bin]] fallback::

    >>> bin_name = get_bin_name(manifest, manifest_path)
    >>> bin_name
    'my-binary'

Resolving workspace-inherited versions::

    >>> root = find_workspace_root(Path("crates/member"))
    >>> if root:
    ...     version = get_workspace_version(root)
    ...     print(f"Workspace version: {version}")
    Workspace version: 2.0.0
"""

from __future__ import annotations

import tomllib
import typing as typ
from pathlib import Path  # noqa: TC003


class ManifestError(Exception):
    """Raised when a Cargo manifest cannot be processed.

    Parameters
    ----------
    path : Path
        Path to the manifest file that caused the error.
    message : str
        Human-readable error description.

    Attributes
    ----------
    path : Path
        The manifest path associated with this error.

    Examples
    --------
    >>> raise ManifestError(Path("Cargo.toml"), "Missing [package] table")
    Traceback (most recent call last):
    ManifestError: Missing [package] table
    """

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def read_manifest(path: Path) -> dict[str, typ.Any]:
    """Load and parse a Cargo.toml manifest file.

    Parameters
    ----------
    path : Path
        Path to the ``Cargo.toml`` file.

    Returns
    -------
    dict[str, Any]
        Parsed TOML content as a nested dictionary.

    Raises
    ------
    ManifestError
        If the file does not exist or contains invalid TOML.

    Examples
    --------
    >>> from pathlib import Path
    >>> manifest = read_manifest(Path("Cargo.toml"))
    >>> "package" in manifest
    True
    """
    if not path.is_file():
        msg = f"Manifest not found: {path}"
        raise ManifestError(path, msg)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        msg = f"Invalid TOML in manifest: {exc}"
        raise ManifestError(path, msg) from exc


def get_package_field(
    manifest: dict[str, typ.Any], field: str, manifest_path: Path
) -> str:
    """Extract a field from the [package] table.

    Parameters
    ----------
    manifest : dict[str, Any]
        Parsed Cargo manifest dictionary.
    field : str
        Name of the field to extract (e.g., ``"name"``, ``"version"``).
    manifest_path : Path
        Path to the manifest file, used for error reporting.

    Returns
    -------
    str
        The field value as a stripped string.

    Raises
    ------
    ManifestError
        If the [package] table is missing or the field is absent/empty.

    Examples
    --------
    >>> from pathlib import Path
    >>> manifest = {"package": {"name": "example", "version": "1.0.0"}}
    >>> get_package_field(manifest, "name", Path("Cargo.toml"))
    'example'
    >>> get_package_field(manifest, "version", Path("Cargo.toml"))
    '1.0.0'
    """
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise ManifestError(
            manifest_path,
            "Manifest missing [package] table",
        )
    value = package.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(
            manifest_path,
            f"package.{field} is missing or empty",
        )
    return value.strip()


def _extract_first_bin_name(bins: object) -> str | None:
    """Extract the name from the first [[bin]] entry if valid."""
    if not isinstance(bins, list) or not bins:
        return None
    first_bin = bins[0]
    if not isinstance(first_bin, dict):
        return None
    bin_name = first_bin.get("name")
    if isinstance(bin_name, str) and bin_name.strip():
        return bin_name.strip()
    return None


def get_bin_name(manifest: dict[str, typ.Any], manifest_path: Path) -> str:
    """Extract the binary name from [[bin]] or [package].name.

    Checks the first ``[[bin]]`` entry for a ``name`` field and falls
    back to ``[package].name`` if no explicit binary target is defined.

    Parameters
    ----------
    manifest : dict[str, Any]
        Parsed Cargo manifest dictionary.
    manifest_path : Path
        Path to the manifest file, used for error reporting.

    Returns
    -------
    str
        The resolved binary name.

    Raises
    ------
    ManifestError
        If neither [[bin]].name nor [package].name can be determined.

    Examples
    --------
    With an explicit [[bin]] entry::

        >>> from pathlib import Path
        >>> manifest = {
        ...     "package": {"name": "my-lib"},
        ...     "bin": [{"name": "my-cli", "path": "src/main.rs"}],
        ... }
        >>> get_bin_name(manifest, Path("Cargo.toml"))
        'my-cli'

    Falling back to [package].name::

        >>> manifest = {"package": {"name": "my-package", "version": "1.0.0"}}
        >>> get_bin_name(manifest, Path("Cargo.toml"))
        'my-package'
    """
    bin_name = _extract_first_bin_name(manifest.get("bin"))
    if bin_name is not None:
        return bin_name
    return get_package_field(manifest, "name", manifest_path)


def find_workspace_root(start_dir: Path) -> Path | None:
    """Locate the nearest ancestor Cargo.toml that declares a workspace.

    Searches upward from ``start_dir`` for a ``Cargo.toml`` containing
    a ``[workspace]`` table.

    Parameters
    ----------
    start_dir : Path
        Directory from which to begin the upward search.

    Returns
    -------
    Path or None
        Path to the workspace root manifest, or ``None`` if not found.

    Examples
    --------
    >>> root = find_workspace_root(Path("crates/member"))
    >>> root
    PosixPath('/project/Cargo.toml')
    """
    directory = start_dir.resolve()
    while True:
        candidate = directory / "Cargo.toml"
        if candidate.exists():
            try:
                with candidate.open("rb") as handle:
                    data = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                data = None
            if isinstance(data, dict) and isinstance(data.get("workspace"), dict):
                return candidate
        if directory.parent == directory:
            return None
        directory = directory.parent


def get_workspace_version(root_manifest: Path) -> str | None:
    """Read the version from [workspace.package] in a workspace manifest.

    Parameters
    ----------
    root_manifest : Path
        Path to the workspace root ``Cargo.toml``.

    Returns
    -------
    str or None
        The workspace version string, or ``None`` if not defined.

    Examples
    --------
    >>> version = get_workspace_version(Path("Cargo.toml"))
    >>> version
    '2.0.0'
    """
    try:
        with root_manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        return None
    package = workspace.get("package")
    if not isinstance(package, dict):
        return None
    version = package.get("version")
    return version.strip() if isinstance(version, str) else None


def _require_package_table(
    manifest: dict[str, typ.Any], manifest_path: Path
) -> dict[str, typ.Any]:
    """Extract and validate the [package] table from a manifest."""
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise ManifestError(manifest_path, "Manifest missing [package] table")
    return package


def _is_workspace_inherited(version: object) -> bool:
    """Check if a version field uses workspace inheritance."""
    return isinstance(version, dict) and version.get("workspace") is True


def _resolve_inherited_version(manifest_path: Path) -> str:
    """Resolve the version from the workspace root manifest."""
    workspace_root = find_workspace_root(manifest_path.parent)
    if workspace_root is None:
        raise ManifestError(
            manifest_path,
            "Could not locate workspace root for inherited version",
        )
    workspace_version = get_workspace_version(workspace_root)
    if workspace_version is None:
        raise ManifestError(
            workspace_root,
            "Workspace manifest missing [workspace.package].version",
        )
    return workspace_version


def _require_version_string(version: object, manifest_path: Path) -> str:
    """Validate and return a version string."""
    if not isinstance(version, str) or not version.strip():
        raise ManifestError(manifest_path, "package.version is missing or empty")
    return version.strip()


def resolve_version(
    manifest: dict[str, typ.Any],
    manifest_path: Path,
) -> str:
    """Resolve the package version, handling workspace inheritance.

    If ``[package].version`` is set to ``{ workspace = true }``, searches
    for the workspace root and reads the version from there.

    Parameters
    ----------
    manifest : dict[str, Any]
        Parsed Cargo manifest dictionary.
    manifest_path : Path
        Path to the manifest file (used for workspace root search).

    Returns
    -------
    str
        The resolved version string.

    Raises
    ------
    ManifestError
        If the version cannot be resolved (missing workspace root or version).

    Examples
    --------
    Direct version::

        >>> manifest = {"package": {"name": "pkg", "version": "1.0.0"}}
        >>> resolve_version(manifest, Path("Cargo.toml"))
        '1.0.0'

    Workspace-inherited version::

        >>> manifest = {"package": {"name": "pkg", "version": {"workspace": True}}}
        >>> resolve_version(manifest, Path("crates/member/Cargo.toml"))
        '2.0.0'
    """
    package = _require_package_table(manifest, manifest_path)
    version = package.get("version")

    if _is_workspace_inherited(version):
        return _resolve_inherited_version(manifest_path)

    return _require_version_string(version, manifest_path)


__all__ = [
    "ManifestError",
    "find_workspace_root",
    "get_bin_name",
    "get_package_field",
    "get_workspace_version",
    "read_manifest",
    "resolve_version",
]
