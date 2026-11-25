"""Reading the TOML documents a preflight declaration may live in.

The declaration is untrusted input from the caller's repository, so every
failure comes back as a finding naming the file rather than as an exception. An
absent optional document is skipped; a present one that cannot be read is
reported, because saying it were missing would hide what the author wrote.
"""

from __future__ import annotations

import typing as typ

from .file_inspection import inspect_path, read_toml
from .ports import Document

if typ.TYPE_CHECKING:
    from pathlib import Path


class DeclarationDocuments:
    """Reads the documents a declaration is looked for in."""

    def read_documents(
        self, root: Path, relatives: tuple[Path, ...]
    ) -> tuple[Document, ...]:
        """Return every one of *relatives* present under *root*, parsed in order."""
        documents: list[Document] = []
        for relative in relatives:
            path = root / relative
            present, regular, problem = inspect_path(path)
            if problem is not None:
                documents.append(Document(path, None, problem))
                continue
            if not present:
                # A file the repository simply does not have is optional: a
                # repository declaring nothing is a normal state, not a refusal.
                continue
            if not regular:
                # A directory or a dangling link at a declaration's path is not
                # absence: something is there and cannot be read, and saying it
                # was missing would hide the declaration the author wrote.
                unreadable = (
                    f"{path} exists but is not a file, so its requirements "
                    f"cannot be read"
                )
                documents.append(Document(path, None, unreadable))
                continue
            document, problem = read_toml(path)
            documents.append(Document(path, document, problem or ""))
        return tuple(documents)
