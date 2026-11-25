"""Reading the mutmut configuration the selected release will use.

The configuration may live in ``pyproject.toml``'s ``[tool.mutmut]`` or in
``setup.cfg``'s ``[mutmut]``. mutmut selects a present ``[tool.mutmut]`` value
and fails when it indexes it, so a value that is not a table is reported rather
than treated as an absent section that falls back to ``setup.cfg``.
"""

from __future__ import annotations

import typing as typ
from configparser import ConfigParser
from configparser import Error as ConfigError

from .file_inspection import inspect_path, read_toml

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Why the mutmut configuration is unknown: neither file that could hold it is
#: present. An unreadable file is reported with its own problem instead.
_NO_CONFIG: typ.Final[str] = (
    "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
    "is present, so what mutmut copies cannot be established"
)


class MutmutConfiguration:
    """Reads the mutmut configuration the run will use."""

    def read_mutmut_configuration(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        table, problem = self._mutmut_table(root)
        return table, problem

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
        present, regular, problem = inspect_path(path)
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
        present, regular, problem = inspect_path(pyproject)
        if problem is not None:
            return None, problem, True
        if not present:
            return None, None, False
        if not regular:
            unusable = f"{pyproject} exists but is not a file, so it cannot be read"
            return None, unusable, True
        document, problem = read_toml(pyproject)
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
