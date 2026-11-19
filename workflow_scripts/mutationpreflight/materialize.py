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


def _escapes(root: Path, source: Path) -> bool:
    """Return whether *source* is a link whose target leaves *root*."""
    return source.is_symlink() and not source.resolve().is_relative_to(root)


def _keep_link(source: Path, target: Path) -> None:
    """Write *source*'s link at *target*, naming the target it already had."""
    target.symlink_to(source.readlink(), target_is_directory=source.is_dir())


def _kept_as_link(root: Path, source: Path, seen: frozenset[Path]) -> bool:
    """Return whether *source* must be copied as a link rather than followed."""
    if not source.is_symlink():
        return False
    if _escapes(root, source):
        return True
    return source.resolve() in seen


def _escaping_ancestor(root: Path, source: Path) -> Path | None:
    """Return the nearest ancestor of *source* that links out of *root*."""
    for ancestor in source.parents:
        if ancestor == root or not ancestor.is_relative_to(root):
            return None
        if _escapes(root, ancestor):
            return ancestor
    return None


def _keep_link_at(destination: Path, root: Path, link: Path) -> None:
    """Write *link* into the sandbox where it sits in the checkout."""
    target = destination / link.relative_to(root)
    if target.is_symlink() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    _keep_link(link, target)


def _copy_source(root: Path, destination: Path, source: Path, target: Path) -> None:
    """Copy *source* to *target*, keeping an escaping ancestor as a link.

    A declared path such as ``tests/escape/secret.txt`` may be reached through
    a link that leaves the checkout. Writing the entry would copy a foreign
    tree into the sandbox, so the link is preserved in its checkout-relative
    place instead, and the entry keeps resolving through it as it does in the
    checkout.
    """
    escape = _escaping_ancestor(root, source)
    if escape is not None:
        _keep_link_at(destination, root, escape)
        return
    _copy_path(root, source, target, frozenset())


def _copy_path(root: Path, source: Path, target: Path, seen: frozenset[Path]) -> None:
    """Copy one entry, applying the containment rule at every level.

    A symlink whose target is inside the checkout is materialized by content,
    because that is what mutmut's ``copytree`` does: it follows links and
    copies what they point at, so a fixture tree linked to a sibling directory
    arrives complete rather than dangling. A link that leaves the checkout is
    kept as a link wherever it appears, so a directory copied whole cannot
    import a tree the declaration never named. A link that would re-enter a
    directory already being copied is kept as a link as well, so a cycle is
    preserved rather than followed forever.

    Parameters
    ----------
    root : Path
        The resolved checkout every link is judged against.
    source : Path
        The entry to copy.
    target : Path
        Where the entry is written in the sandbox.
    seen : frozenset[Path]
        The directories this branch is already copying, by resolved path.

    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if _kept_as_link(root, source, seen):
        _keep_link(source, target)
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        inner = seen | {source.resolve()}
        for child in sorted(source.iterdir()):
            if child.name in EXCLUDED_DIRECTORIES:
                continue
            _copy_path(root, child, target / child.name, inner)
        return
    shutil.copy2(source, target)


def _copy_entry(root: Path, repo_root: Path, destination: Path, entry: str) -> None:
    """Copy one contract entry, keeping its place in the tree."""
    source = repo_root / entry
    if not source.exists() and not source.is_symlink():
        return
    _copy_source(root, destination, source, destination / entry)


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
        root = repo_root.resolve()
        for entry in contract.paths:
            _copy_entry(root, repo_root, destination, entry)
        for pattern in contract.globs:
            for source in sorted(repo_root.glob(pattern)):
                if source.is_file():
                    # A glob match receives the same containment rule as a
                    # declared entry, so an escaping link stays a link.
                    _copy_source(root, destination, source, destination / source.name)
    except OSError as error:
        shutil.rmtree(destination, ignore_errors=True)
        return None, (f"the check sandbox could not be materialized: {error}",)
    return destination, ()


def discard(sandbox: Path) -> None:
    """Remove a materialized sandbox once the checks have run."""
    shutil.rmtree(sandbox, ignore_errors=True)
