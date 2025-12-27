"""Utilities for preparing and writing staging workflow outputs."""

from __future__ import annotations

import json
import typing as typ

from .errors import StageError

if typ.TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "RESERVED_OUTPUT_KEYS",
    "prepare_output_data",
    "validate_no_reserved_key_collisions",
    "write_github_output",
]


RESERVED_OUTPUT_KEYS: frozenset[str] = frozenset(
    {
        "artifact_dir",
        "dist_dir",
        "staged_files",
        "artefact_map",
        "checksum_map",
    }
)


def prepare_output_data(
    staging_dir: Path,
    staged_paths: list[Path],
    outputs: dict[str, Path],
    checksums: dict[str, str],
) -> dict[str, str | list[str]]:
    """Assemble workflow outputs describing the staged artefacts.

    Parameters
    ----------
    staging_dir
        Directory that now contains all staged artefacts.
    staged_paths
        Collection of artefact paths copied into ``staging_dir``.
    outputs
        Mapping of configured GitHub Action output keys to staged artefact
        destinations.
    checksums
        Mapping of staged artefact file names to their checksum digests.

    Returns
    -------
    dict[str, str | list[str]]
        Dictionary describing the staging results ready to be exported to the
        GitHub Actions output file.
    """
    staged_file_names: list[str] = [path.name for path in sorted(staged_paths)]
    artefact_map_json = json.dumps(
        {key: path.as_posix() for key, path in sorted(outputs.items())}
    )
    checksum_map_json = json.dumps(dict(sorted(checksums.items())))

    return {
        "artifact_dir": staging_dir.as_posix(),
        "dist_dir": staging_dir.parent.as_posix(),
        "staged_files": staged_file_names,
        "artefact_map": artefact_map_json,
        "checksum_map": checksum_map_json,
    } | {key: path.as_posix() for key, path in outputs.items()}


def validate_no_reserved_key_collisions(outputs: dict[str, Path]) -> None:
    """Ensure user-defined outputs avoid the reserved workflow output keys.

    Parameters
    ----------
    outputs
        Mapping of configured GitHub Action output keys to staged artefact
        destinations.

    Raises
    ------
    StageError
        Raised when a user-defined output key overlaps with reserved keys.
    """
    if collisions := sorted(outputs.keys() & RESERVED_OUTPUT_KEYS):
        msg = f"Artefact outputs collide with reserved keys: {collisions}"
        raise StageError(msg)


def _format_list_output(key: str, values: list[str]) -> str:
    """Format a list value for GitHub Actions output using heredoc syntax."""
    delimiter = f"gh_{key.upper()}"
    content = "\n".join(values)
    return f"{key}<<{delimiter}\n{content}\n{delimiter}\n"


def _format_scalar_output(
    key: str, value: str, *, normalize_windows_paths: bool
) -> str:
    """Format a scalar value for GitHub Actions output with escaping."""
    if normalize_windows_paths:
        value = value.replace("\\", "/")
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"{key}={escaped}\n"


def write_github_output(
    file: Path,
    values: dict[str, str | list[str]],
    *,
    normalize_windows_paths: bool = False,
) -> None:
    """Append ``values`` to the GitHub Actions output ``file``.

    Parameters
    ----------
    file
        Target ``GITHUB_OUTPUT`` file that receives the exported values.
    values
        Mapping of output names to values ready for GitHub Actions
        consumption.
    normalize_windows_paths
        When True, convert backslashes to forward slashes in path values.
    """
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as handle:
        for key, value in sorted(values.items()):
            if isinstance(value, list):
                handle.write(_format_list_output(key, value))
            else:
                handle.write(
                    _format_scalar_output(
                        key, value, normalize_windows_paths=normalize_windows_paths
                    )
                )
