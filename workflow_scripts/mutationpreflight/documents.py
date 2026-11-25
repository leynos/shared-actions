"""Reading the documents a judgement parses, and locating declared paths.

Both the declaration, the lock and the mutmut configuration are untrusted input
from the caller's repository, so every failure comes back as a finding naming
the file rather than as an exception.
"""

from __future__ import annotations

import os
import stat
import tomllib
import typing as typ
from configparser import ConfigParser
from configparser import Error as ConfigError
from pathlib import Path

from .ports import Document, LocatedPath

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
            present, regular, problem = self._inspect(path)
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

    def _resolution(self, path: Path) -> tuple[bool, Path, str | None]:
        """Return whether *path* reaches something, where it lands, and why not.

        A dangling link is present but delivers nothing, so it is reported as
        not resolving: a declaration that names it asks for a file the checkout
        cannot deliver. The path it points at is still named, so a finding can
        say what the link expected.
        """
        problem = self._inspect(path)[2]
        if problem is not None:
            return False, path, problem
        try:
            return True, path.resolve(strict=True), None
        except (FileNotFoundError, NotADirectoryError):
            return False, Path(os.path.realpath(path)), None
        except OSError as error:
            return False, path, f"{path} could not be inspected: {error}"

    def is_directory(self, path: Path) -> tuple[bool, str | None]:
        """Return whether *path* is a directory, or why it could not be inspected."""
        try:
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return False, None
        except OSError as error:
            return False, f"{path} could not be inspected: {error}"
        return stat.S_ISDIR(info.st_mode), None

    @staticmethod
    def _inspect(path: Path) -> tuple[bool, bool, str | None]:
        """Return presence, regularity and why *path* could not be inspected.

        The filesystem answers through ``stat``, not through ``Path.exists`` or
        ``Path.is_file``: from Python 3.14 those predicates swallow every
        ``OSError`` and answer ``False``, so a declaration the process may not
        inspect would be reported as an absent one. The three answers are kept
        apart for the same reason: a directory or a dangling link is not
        absence, and saying it were would hide a declaration the author wrote.
        """
        try:
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return FileDocuments._target_missing(path)
        except OSError as error:
            return False, False, f"{path} could not be inspected: {error}"
        return True, stat.S_ISREG(info.st_mode), None

    @staticmethod
    def _target_missing(path: Path) -> tuple[bool, bool, str | None]:
        """Return presence for a path whose target the filesystem cannot find.

        ``stat`` follows a symbolic link, so a dangling one reads as absent.
        ``lstat`` reports the link itself, which is present and unreadable
        rather than missing.
        """
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return False, False, None
        except OSError as error:
            return False, False, f"{path} could not be inspected: {error}"
        return True, stat.S_ISREG(info.st_mode), None

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
        present, regular, problem = self._inspect(lock)
        if problem is not None:
            return [], problem
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
        return self._package_records(document or {})

    @staticmethod
    def _package_records(
        document: dict[str, object],
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return the lock's package tables, or why its shape is unusable.

        A record that is not a table is skipped: the lock's own shape has
        already been refused if the entry is not an array, so what is left is
        noise a later uv may add.
        """
        packages = document.get("package", [])
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
        return {
            key: self._section_values(section[key])
            for key in ("source_paths", "paths_to_mutate", "also_copy")
            if key in section
        }

    @staticmethod
    def _section_values(raw: str) -> list[str]:
        """Return one ``[mutmut]`` value as the list mutmut reads."""
        # mutmut splits a multi-line value on newlines and wraps a single line
        # as one entry, so a comma-separated line is one path too.
        return [item for item in raw.split("\n") if item] if "\n" in raw else [raw]

    def _setup_cfg_table(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return ``setup.cfg``'s ``[mutmut]`` section, or why it went unread."""
        path = root / "setup.cfg"
        present, regular, problem = self._inspect(path)
        if problem is not None:
            return None, problem
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

    def _pyproject_table(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None, bool]:
        """Return ``[tool.mutmut]``, why it is unusable, and whether pyproject exists.

        The third value tells absence from a present file without the section,
        which ``_mutmut_table`` needs to choose between ``setup.cfg`` and its
        "nothing declares a mutmut configuration" answer.
        """
        pyproject = root / "pyproject.toml"
        present, regular, problem = self._inspect(pyproject)
        if problem is not None:
            return None, problem, True
        if not present:
            return None, None, False
        if not regular:
            unusable = f"{pyproject} exists but is not a file, so it cannot be read"
            return None, unusable, True
        document, problem = self._read_document(pyproject)
        if problem is not None:
            return None, problem, True
        table, unusable = self._nested_table(document or {}, "tool", "mutmut")
        if unusable is not None:
            return None, f"{pyproject}: {unusable}", True
        return table, None, True

    def _mutmut_table(self, root: Path) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        table, problem, _ = self._pyproject_table(root)
        if table is not None or problem is not None:
            return table, problem
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
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the table at *keys*, or why the value found there is not one.

        The first value is ``None`` both when nothing occupies the path and
        when something unusable does, and the second names the latter. A
        caller needs that difference: mutmut falls back to ``setup.cfg`` when
        the path is absent, but selects a present value and fails when it
        indexes it, so treating a non-table as absent would certify a copy set
        mutmut never uses.
        """
        unusable = (
            f"[{'.'.join(keys)}] is not a table, so what mutmut copies cannot "
            f"be established"
        )
        current: object = document
        for key in keys:
            if not isinstance(current, dict):
                return None, unusable
            if key not in current:
                return None, None
            current = typ.cast("dict[str, object]", current)[key]
        if isinstance(current, dict):
            return typ.cast("dict[str, object]", current), None
        return None, unusable
