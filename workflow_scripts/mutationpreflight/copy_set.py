"""Establishing which paths mutmut's sandbox will contain.

mutmut copies a fixed set of root files and globs, then everything named in
``source_paths`` and ``also_copy``. Reading that configuration is a query of
its own: the sandbox phase compares against it, and nothing else needs to know
whether the answer came from ``pyproject.toml`` or ``setup.cfg``.
"""

from __future__ import annotations

import typing as typ
from configparser import ConfigParser, NoOptionError, NoSectionError

from .constants import MUTMUT_DEFAULT_COPIES
from .models import DeclarationError
from .schema import strings
from .toml_documents import nested_table, read_document

if typ.TYPE_CHECKING:
    from pathlib import Path

_NO_CONFIG: typ.Final[str] = (
    "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
    "is present, so what mutmut copies cannot be established"
)
_NO_SOURCE_PATHS: typ.Final[str] = (
    "the mutmut configuration declares no source_paths, so the copy set "
    "cannot be established; mutmut would guess it"
)


def _setup_cfg_table(root: Path) -> dict[str, object] | None:
    """Return ``setup.cfg``'s ``[mutmut]`` section, flattened as mutmut reads it."""
    path = root / "setup.cfg"
    if not path.is_file():
        return None
    parser = ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        section = parser["mutmut"]
    except (OSError, UnicodeDecodeError, NoSectionError, NoOptionError):
        return None
    table: dict[str, object] = {}
    for key in ("source_paths", "also_copy"):
        if key not in section:
            continue
        raw = section[key]
        # mutmut splits a multi-line value on newlines and wraps a single line
        # as one entry, so a comma-separated line is one path here too.
        table[key] = (
            [item for item in raw.split("\n") if item] if "\n" in raw else [raw]
        )
    return table


def _mutmut_table(root: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return the mutmut configuration the run will use, or why it is unknown."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        document, problem = read_document(pyproject)
        if problem is not None:
            return None, problem
        table = nested_table(document or {}, "tool", "mutmut")
        if table is not None:
            return table, None
    table = _setup_cfg_table(root)
    if table is None:
        return None, _NO_CONFIG
    return table, None


def mutmut_copy_set(root: Path) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """Return the paths mutmut's sandbox will contain, or why they are unknown.

    Parameters
    ----------
    root : Path
        The checkout holding the mutmut configuration.

    Returns
    -------
    tuple[tuple[str, ...] | None, tuple[str, ...]]
        The copied paths when they can be established, or ``None`` with the
        findings that explain why they cannot.

    """
    table, problem = _mutmut_table(root)
    if table is None:
        return None, (problem or "the mutmut configuration could not be read",)
    try:
        source_paths = strings(table.get("source_paths"), "[tool.mutmut] source_paths")
        also_copy = strings(table.get("also_copy"), "[tool.mutmut] also_copy")
    except DeclarationError as error:
        return None, (str(error),)
    if not source_paths:
        return None, (_NO_SOURCE_PATHS,)
    return (*MUTMUT_DEFAULT_COPIES, *source_paths, *also_copy), ()
