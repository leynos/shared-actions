"""Materializing the sandbox mutmut will hand its own tests.

A check that runs in the checkout can pass on a file the sandbox never
receives, then fail inside ``mutants/``. Copying the declared contract into a
fresh directory first makes the check run where the mutation run will run it.
"""

from __future__ import annotations

import shutil
import tempfile
import typing as typ
from pathlib import Path

from .constants import EXCLUDED_DIRECTORIES

if typ.TYPE_CHECKING:
    from .copy_set import CopyContract


def _ignored(_directory: str, names: list[str]) -> set[str]:
    """Return the entries never copied into the sandbox."""
    return {name for name in names if name in EXCLUDED_DIRECTORIES}


def _copy_entry(repo_root: Path, destination: Path, entry: str) -> None:
    """Copy one contract entry, keeping its place in the tree.

    A symlink is copied as a link, not followed: the sandbox must carry what
    the declaration named, so a link that leaves the checkout is still visible
    to the containment check rather than silently replaced by its target.
    """
    source = repo_root / entry
    if not source.exists() and not source.is_symlink():
        return
    target = destination / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(source.readlink(), target_is_directory=source.is_dir())
        return
    if source.is_dir():
        shutil.copytree(
            source, target, symlinks=True, dirs_exist_ok=True, ignore=_ignored
        )
        return
    shutil.copy2(source, target, follow_symlinks=False)


def materialize(
    repo_root: Path, contract: CopyContract
) -> tuple[Path | None, tuple[str, ...]]:
    """Copy *contract* into a fresh directory, returning it or why not.

    Parameters
    ----------
    repo_root : Path
        The checkout the contract describes.
    contract : CopyContract
        The selected release's copy contract.

    Returns
    -------
    tuple[Path | None, tuple[str, ...]]
        The materialized sandbox, or ``None`` and why it could not be built.

    """
    try:
        destination = Path(tempfile.mkdtemp(prefix="mutation-preflight-sandbox-"))
    except OSError as error:
        return None, (f"the check sandbox could not be created: {error}",)
    try:
        for entry in contract.paths:
            _copy_entry(repo_root, destination, entry)
        for pattern in contract.globs:
            for source in sorted(repo_root.glob(pattern)):
                if source.is_file():
                    shutil.copy2(
                        source, destination / source.name, follow_symlinks=False
                    )
    except OSError as error:
        shutil.rmtree(destination, ignore_errors=True)
        return None, (f"the check sandbox could not be materialized: {error}",)
    return destination, ()


def discard(sandbox: Path) -> None:
    """Remove a materialized sandbox once the checks have run."""
    shutil.rmtree(sandbox, ignore_errors=True)
