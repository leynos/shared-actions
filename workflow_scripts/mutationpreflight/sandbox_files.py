"""Building the sandbox a check runs in, and removing it afterwards.

The copy follows mutmut's own rules: a symbolic link whose target is inside the
checkout is materialized by content, and one that leaves it is kept as a link,
so the sandbox carries what the checkout declares and nothing it does not.
"""

from __future__ import annotations

import shutil
import tempfile
import typing as typ
from pathlib import Path

from .constants import EXCLUDED_DIRECTORIES

if typ.TYPE_CHECKING:
    from .copy_set import CopyContract


class SandboxMaterializer:
    """Builds the sandbox a check runs in, and removes it afterwards."""

    def materialize(
        self, root: Path, contract: CopyContract
    ) -> tuple[Path | None, tuple[str, ...]]:
        """Copy *contract* into a fresh directory, returning it or why not."""
        try:
            destination = Path(tempfile.mkdtemp(prefix="mutation-preflight-sandbox-"))
        except OSError as error:
            return None, (f"the check sandbox could not be created: {error}",)
        try:
            refused = self._copy_contract(root, contract, destination)
        except OSError as error:
            shutil.rmtree(destination, ignore_errors=True)
            return None, (f"the check sandbox could not be materialized: {error}",)
        if refused is not None:
            shutil.rmtree(destination, ignore_errors=True)
            return None, (refused,)
        return destination, ()

    def discard(self, sandbox: Path) -> None:
        """Remove a materialized sandbox once the checks have run."""
        shutil.rmtree(sandbox, ignore_errors=True)

    def _copy_contract(
        self, root: Path, contract: CopyContract, destination: Path
    ) -> str | None:
        """Copy *contract* into *destination*, or say why an entry is refused."""
        anchor = root.resolve()
        for entry in contract.paths:
            problem = self._copy_contract_entry(anchor, root, destination, entry)
            if problem is not None:
                return problem
        for source in self._glob_sources(root, contract.globs):
            # A glob match receives the same containment rule as a declared
            # entry, so an escaping link stays a link.
            self._copy_source(anchor, destination, source, destination / source.name)
        return None

    def _copy_contract_entry(
        self, anchor: Path, root: Path, destination: Path, entry: str
    ) -> str | None:
        """Copy one contract entry, or say why it must not be copied."""
        target = destination / entry
        if target.is_symlink() or target.exists():
            # An earlier, overlapping entry already wrote this path, and the
            # first write is the one that counts.
            return None
        if self._outside(destination, entry):
            # The contract is validated before it reaches here; this is the
            # second check, so a future caller cannot copy through a link the
            # first one did not foresee.
            return (
                f"the mutmut copy contract entry {entry!r} would write outside "
                f"the sandbox"
            )
        self._copy_entry(anchor, root, destination, entry)
        return None

    def _glob_sources(self, root: Path, globs: tuple[str, ...]) -> list[Path]:
        """Return the files the contract's root globs select, in path order."""
        found: list[Path] = []
        for pattern in globs:
            found.extend(root.glob(pattern))
        return sorted(source for source in found if source.is_file())

    def _outside(self, destination: Path, entry: str) -> bool:
        """Return whether copying *entry* would write outside *destination*."""
        try:
            target = (destination / entry).resolve()
            return not target.is_relative_to(destination.resolve())
        except OSError:
            return True

    def _copy_entry(
        self, root: Path, repo_root: Path, destination: Path, entry: str
    ) -> None:
        """Copy one contract entry, keeping its place in the tree."""
        source = repo_root / entry
        if not source.exists() and not source.is_symlink():
            return
        self._copy_source(root, destination, source, destination / entry)

    def _copy_source(
        self, root: Path, destination: Path, source: Path, target: Path
    ) -> None:
        """Copy *source* to *target*, keeping an escaping ancestor as a link."""
        escape = self._escaping_ancestor(root, source)
        if escape is not None:
            self._keep_link_at(destination, root, escape)
            return
        self._copy_path(root, source, target, frozenset())

    def _copy_path(
        self, root: Path, source: Path, target: Path, seen: frozenset[Path]
    ) -> None:
        """Copy one entry, applying the containment rule at every level.

        A symlink whose target is inside the checkout is materialized by
        content, because that is what mutmut's ``copytree`` does: it follows
        links and copies what they point at, so a fixture tree linked to a
        sibling directory arrives complete rather than dangling. A link that
        leaves the checkout is kept as a link wherever it appears, so a
        directory copied whole cannot import a tree the declaration never
        named. A link that would re-enter a directory already being copied is
        kept as a link as well, so a cycle is preserved rather than followed
        forever.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._kept_as_link(root, source, seen):
            self._keep_link_unless_present(source, target)
            return
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            inner = seen | {source.resolve()}
            for child in sorted(source.iterdir()):
                if child.name in EXCLUDED_DIRECTORIES:
                    continue
                self._copy_path(root, child, target / child.name, inner)
            return
        shutil.copy2(source, target)

    def _kept_as_link(self, root: Path, source: Path, seen: frozenset[Path]) -> bool:
        """Return whether *source* must be copied as a link rather than followed."""
        if not source.is_symlink():
            return False
        if self._escapes(root, source):
            return True
        return source.resolve() in seen

    def _keep_link_unless_present(self, source: Path, target: Path) -> None:
        """Write *source*'s link at *target* unless the path is already written.

        Overlapping contract entries reach the same sandbox path: the default
        ``tests`` entry and an ``also_copy`` entry naming a link inside it both
        copy that link. The first write is the one that counts, so a second is
        left alone rather than raising on the existing path.
        """
        if target.is_symlink() or target.exists():
            return
        self._keep_link(source, target)

    def _keep_link(self, source: Path, target: Path) -> None:
        """Write *source*'s link at *target*, naming the target it already had."""
        target.symlink_to(source.readlink(), target_is_directory=source.is_dir())

    def _escapes(self, root: Path, source: Path) -> bool:
        """Return whether *source* is a link whose target leaves *root*."""
        return source.is_symlink() and not source.resolve().is_relative_to(root)

    def _escaping_ancestor(self, root: Path, source: Path) -> Path | None:
        """Return the nearest ancestor of *source* that links out of *root*."""
        for ancestor in source.parents:
            if ancestor == root or not ancestor.is_relative_to(root):
                return None
            if self._escapes(root, ancestor):
                return ancestor
        return None

    def _keep_link_at(self, destination: Path, root: Path, link: Path) -> None:
        """Write *link* into the sandbox where it sits in the checkout."""
        target = destination / link.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._keep_link_unless_present(link, target)
