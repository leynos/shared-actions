"""Path resolution helpers for artefact staging."""

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


def _windows_root(rendered: str) -> tuple[Path, tuple[str, ...]]:
    """Return the Windows drive root and path components for ``rendered``."""
    windows_candidate = PureWindowsPath(rendered)
    root = Path(windows_candidate.anchor or "/")
    relative_parts = windows_candidate.parts[1:]
    return root, relative_parts


def _parts_to_pattern(parts: tuple[str, ...]) -> str:
    """Convert path parts to a glob pattern string."""
    return PurePosixPath(*parts).as_posix() if parts else "*"


def _resolve_absolute_glob(candidate: Path) -> typ.Iterator[Path]:
    """Resolve a glob for an absolute path."""
    root = Path(candidate.anchor or "/")
    relative_parts = candidate.parts[1:]
    pattern = _parts_to_pattern(relative_parts)
    return root.glob(pattern)


def _resolve_windows_glob(rendered: str) -> typ.Iterator[Path]:
    """Resolve a glob for a Windows absolute path."""
    root, relative_parts = _windows_root(rendered)
    pattern = _parts_to_pattern(relative_parts)
    return root.glob(pattern)


def _resolve_glob_pattern(
    workspace: Path, rendered: str, candidate: Path
) -> Path | None:
    """Resolve a glob ``rendered`` against ``workspace``."""
    if candidate.is_absolute():
        matches = _resolve_absolute_glob(candidate)
    elif PureWindowsPath(rendered).is_absolute():
        matches = _resolve_windows_glob(rendered)
    else:
        matches = workspace.glob(rendered)
    return _newest_file(matches)


def _resolve_direct_path(
    workspace: Path, rendered: str, candidate: Path
) -> Path | None:
    """Resolve a direct ``rendered`` path relative to ``workspace``."""
    if candidate.is_absolute():
        base = candidate
    elif PureWindowsPath(rendered).is_absolute():
        root, relative_parts = _windows_root(rendered)
        base = root.joinpath(*relative_parts)
    else:
        base = workspace / candidate
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
