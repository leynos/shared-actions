"""Reading the documents a judgement parses, and locating declared paths.

Both the declaration, the lock and the mutmut configuration are untrusted input
from the caller's repository, so every failure comes back as a finding naming
the file rather than as an exception.
"""

from __future__ import annotations

import tomllib
import typing as typ
from configparser import ConfigParser
from configparser import Error as ConfigError

from .ports import Document, LocatedPath

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Why the mutmut configuration is unknown: neither file that could hold it is
#: present. An unreadable file is reported with its own problem instead.
_NO_CONFIG: typ.Final[str] = (
    "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
    "is present, so what mutmut copies cannot be established"
)


class FileDocuments:
    """Reads the documents a judgement parses, and locates declared paths."""

    def read_documents(
        self, root: Path, relatives: tuple[Path, ...]
    ) -> tuple[Document, ...]:
        """Return every one of *relatives* present under *root*, parsed in order."""
        documents: list[Document] = []
        for relative in relatives:
            path = root / relative
            try:
                present = path.exists() or path.is_symlink()
                is_file = path.is_file()
            except OSError as error:
                documents.append(
                    Document(path, None, f"{path} could not be inspected: {error}")
                )
                continue
            if not present:
                # A file the repository simply does not have is optional: a
                # repository declaring nothing is a normal state, not a refusal.
                continue
            if not is_file:
                # A directory or a dangling link at a declaration's path is not
                # absence: something is there and cannot be read, and saying it
                # was missing would hide the declaration the author wrote.
                unreadable = (
                    f"{path} exists but is not a file, so its requirements "
                    f"cannot be read"
                )
                documents.append(Document(path, None, unreadable))
                continue
            document, problem = self._read_document(path)
            documents.append(Document(path, document, problem or ""))
        return tuple(documents)

    def read_lock_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read."""
        packages, problem = self._locked_packages(root)
        return packages, problem

    def read_mutmut_configuration(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        table, problem = self._mutmut_table(root)
        return table, problem

    def locate(
        self, root: Path, declared: str
    ) -> tuple[LocatedPath | None, str | None]:
        """Return what *declared* is under *root*, or why it could not be resolved."""
        try:
            anchor = root.resolve()
            resolved = (anchor / declared).resolve()
            present = (root / declared).exists()
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
            return path.is_dir(), None
        except OSError as error:
            return False, f"{path} could not be inspected: {error}"

    def _read_document(self, path: Path) -> tuple[dict[str, object] | None, str | None]:
        """Return *path* parsed as TOML, or why it could not be read."""
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            return None, f"{path} could not be read: {error}"
        return parsed, None

    def _locked_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read."""
        lock = root / "uv.lock"
        try:
            present = lock.exists() or lock.is_symlink()
            regular = lock.is_file()
        except OSError as error:
            return [], f"uv.lock could not be inspected: {error}"
        if not present:
            return [], "uv.lock is absent, so no locked revision can be established"
        if not regular:
            return [], (
                "uv.lock exists but is not a file, so no locked revision can be "
                "established"
            )
        document, problem = self._read_document(lock)
        if problem is not None:
            return [], problem
        packages = (document or {}).get("package", [])
        if not isinstance(packages, list):
            return [], "uv.lock's package entry is not an array"
        records = typ.cast("list[object]", packages)
        tables = [item for item in records if isinstance(item, dict)]
        return [typ.cast("dict[str, object]", item) for item in tables], None

    def _flattened_section(self, parser: ConfigParser) -> dict[str, object] | None:
        """Return the parsed ``[mutmut]`` section, or ``None`` when it is absent."""
        if not parser.has_section("mutmut"):
            return None
        section = parser["mutmut"]
        table: dict[str, object] = {}
        for key in ("source_paths", "paths_to_mutate", "also_copy"):
            if key not in section:
                continue
            raw = section[key]
            # mutmut splits a multi-line value on newlines and wraps a single
            # line as one entry, so a comma-separated line is one path too.
            table[key] = (
                [item for item in raw.split("\n") if item] if "\n" in raw else [raw]
            )
        return table

    def _setup_cfg_table(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return ``setup.cfg``'s ``[mutmut]`` section, or why it went unread."""
        path = root / "setup.cfg"
        try:
            present = path.exists() or path.is_symlink()
            regular = path.is_file()
        except OSError as error:
            return None, f"{path} could not be inspected: {error}"
        if not present:
            return None, None
        if not regular:
            return None, f"{path} exists but is not a file, so it cannot be read"
        parser = ConfigParser()
        try:
            # read_text surfaces an open failure, which ConfigParser.read
            # would skip silently, and reading a value can raise
            # InterpolationSyntaxError, a ConfigError.
            parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
            table = self._flattened_section(parser)
        except (OSError, UnicodeDecodeError, ConfigError) as error:
            return None, f"{path} could not be read: {error}"
        return table, None

    def _mutmut_table(self, root: Path) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        pyproject = root / "pyproject.toml"
        try:
            present = pyproject.exists() or pyproject.is_symlink()
            regular = pyproject.is_file()
        except OSError as error:
            return None, f"{pyproject} could not be inspected: {error}"
        if present and not regular:
            return None, (f"{pyproject} exists but is not a file, so it cannot be read")
        if regular:
            document, problem = self._read_document(pyproject)
            if problem is not None:
                return None, problem
            table = self._nested_table(document or {}, "tool", "mutmut")
            if table is not None:
                return table, None
        table, problem = self._setup_cfg_table(root)
        if table is not None:
            return table, None
        if problem is not None:
            # An unreadable setup.cfg is not the same fact as one without a
            # [mutmut] section, and saying the section is absent would mislead.
            return None, problem
        return None, _NO_CONFIG

    def _nested_table(
        self, document: dict[str, object], *keys: str
    ) -> dict[str, object] | None:
        """Return the table at *keys*, or ``None`` when the path is not a table."""
        current: object = document
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return None
            current = typ.cast("dict[str, object]", current)[key]
        return (
            typ.cast("dict[str, object]", current)
            if isinstance(current, dict)
            else None
        )
