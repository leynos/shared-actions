r"""Path resolution helpers for artefact staging.

This module provides utilities for resolving artefact paths during release
staging. It handles glob patterns, absolute paths (POSIX and Windows), and
relative paths, always resolving against a workspace directory.

Key behaviours:
- Glob patterns (e.g., ``target/release/*.exe``) match the newest file
- Absolute paths are resolved directly without workspace prefix
- Relative paths are resolved relative to the workspace directory
- Windows-style paths (e.g., ``C:\path``) are detected and handled

Example usage::

    from pathlib import Path
    from stage_common.resolution import match_candidate_path

    workspace = Path("/home/runner/work/myproject")

    # Direct path resolution
    binary = match_candidate_path(workspace, "target/release/myapp")

    # Glob pattern resolution (returns newest match)
    binary = match_candidate_path(workspace, "target/release/myapp*")

    # Absolute path (ignores workspace)
    binary = match_candidate_path(workspace, "/usr/local/bin/myapp")
"""

from __future__ import annotations

import glob
import typing as typ
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = ["match_candidate_path"]


def _newest_file(candidates: typ.Iterable[Path]) -> Path | None:
    """Return the newest file from ``candidates``."""
    best_path: Path | None = None
    best_key: tuple[int, str] | None = None
    for path in candidates:
        if not path.is_file():
            continue
        try:
            key = (int(path.stat().st_mtime_ns), path.as_posix())
        except OSError:
            key = (0, path.as_posix())
        if best_key is None or key > best_key:
            best_key = key
            best_path = path
    return best_path


def _split_root_and_parts(
    workspace: Path, rendered: str, candidate: Path
) -> tuple[Path, tuple[str, ...]]:
    """Extract the root directory and relative path parts from a path.

    Handles three path interpretation cases:
    - Absolute POSIX paths: root is the anchor, parts are remaining components
    - Windows absolute paths: root is the drive, parts are remaining components
    - Relative paths: root is workspace, parts are the path components
    """
    if candidate.is_absolute():
        root = Path(candidate.anchor or "/")
        return root, candidate.parts[1:]

    windows_path = PureWindowsPath(rendered)
    if windows_path.is_absolute():
        root = Path(windows_path.anchor or "/")
        return root, windows_path.parts[1:]

    return workspace, candidate.parts


def _resolve_glob_pattern(
    workspace: Path, rendered: str, candidate: Path
) -> Path | None:
    """Resolve a glob ``rendered`` against ``workspace``."""
    root, parts = _split_root_and_parts(workspace, rendered, candidate)
    pattern = PurePosixPath(*parts).as_posix() if parts else "*"
    return _newest_file(root.glob(pattern))


def _resolve_direct_path(
    workspace: Path, rendered: str, candidate: Path
) -> Path | None:
    """Resolve a direct ``rendered`` path relative to ``workspace``."""
    root, parts = _split_root_and_parts(workspace, rendered, candidate)
    base = root.joinpath(*parts) if parts else root
    return base if base.is_file() else None


def match_candidate_path(workspace: Path, rendered: str) -> Path | None:
    """Return the newest path matching ``rendered`` relative to ``workspace``.

    Parameters
    ----------
    workspace
        Root directory for relative path resolution.
    rendered
        Path pattern to match (may contain glob wildcards).

    Returns
    -------
    Path | None
        The matched path, or None if no match was found.
    """
    candidate = Path(rendered)
    resolver = (
        _resolve_glob_pattern if glob.has_magic(rendered) else _resolve_direct_path
    )
    return resolver(workspace, rendered, candidate)
