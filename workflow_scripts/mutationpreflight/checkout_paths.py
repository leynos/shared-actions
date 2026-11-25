"""Locating a declared path in the checkout, and inspecting a directory.

A path the process may not inspect is a refusal rather than an absence, and a
dangling link does not satisfy a declaration: the link is present, but nothing
it names is there to use.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .file_inspection import inspect_path
from .ports import LocatedPath


class CheckoutPaths:
    """Answers where a declared path is in the checkout, without copying it."""

    def locate(
        self, root: Path, declared: str
    ) -> tuple[LocatedPath | None, str | None]:
        """Return what *declared* is under *root*, or why it could not be resolved."""
        present, resolved, problem = self._resolution(root / declared)
        if problem is not None:
            return None, problem
        try:
            anchor = root.resolve()
        except OSError as error:
            return None, f"{declared!r} could not be inspected: {error}"
        inside = resolved.is_relative_to(anchor)
        return (
            LocatedPath(
                exists=present,
                resolved=str(resolved),
                inside_root=inside,
                parts=resolved.relative_to(anchor).parts if inside else (),
            ),
            None,
        )

    def is_directory(self, path: Path) -> tuple[bool, str | None]:
        """Return whether *path* is a directory, or why it could not be inspected."""
        try:
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return False, None
        except OSError as error:
            return False, f"{path} could not be inspected: {error}"
        return stat.S_ISDIR(info.st_mode), None

    def _resolution(self, path: Path) -> tuple[bool, Path, str | None]:
        """Return whether *path* reaches something, where it lands, and why not.

        A dangling link is present but delivers nothing, so it is reported as
        not resolving: a declaration that names it asks for a file the checkout
        cannot deliver. The path it points at is still named, so a finding can
        say what the link expected.
        """
        problem = inspect_path(path)[2]
        if problem is not None:
            return False, path, problem
        try:
            return True, path.resolve(strict=True), None
        except (FileNotFoundError, NotADirectoryError):
            return False, Path(os.path.realpath(path)), None
        except OSError as error:
            return False, path, f"{path} could not be inspected: {error}"
