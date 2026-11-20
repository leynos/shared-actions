"""Establishing which paths mutmut's sandbox will contain.

mutmut copies a fixed set of root files and globs, then everything named in
``source_paths`` and ``also_copy``. Reading that configuration is a query of
its own: the sandbox phase compares against it, and nothing else needs to know
whether the answer came from ``pyproject.toml`` or ``setup.cfg``.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ
from configparser import ConfigParser
from configparser import Error as ConfigError

from .constants import MUTMUT_COPY_CONTRACTS
from .models import DeclarationError
from .schema import strings
from .toml_documents import nested_table, read_document

if typ.TYPE_CHECKING:
    from pathlib import Path

#: A ``major.minor`` release prefix, with an optional leading ``v``.
_VERSION_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"\s*v?(\d+)\.(\d+)")


@dc.dataclass(frozen=True, slots=True)
class CopyContract:
    """What one mutmut release copies, as a sandbox can be judged against it.

    Attributes
    ----------
    paths : tuple[str, ...]
        Everything the release copies by default, including the configured
        ``source_paths`` and ``also_copy`` entries.
    globs : tuple[str, ...]
        Root-level globs the release adds to that set.

    """

    paths: tuple[str, ...]
    globs: tuple[str, ...]


def copy_contract(version: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Return the default copies and globs for *version*, when it is known.

    Parameters
    ----------
    version : str
        The mutmut release the run selects, such as ``3.7.2``.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]] | None
        The release's default copies and root globs, or ``None`` when this
        helper does not know that release's contract.

    """
    match = _VERSION_PREFIX.match(version)
    if match is None:
        return None
    return MUTMUT_COPY_CONTRACTS.get(f"{match.group(1)}.{match.group(2)}")


_NO_CONFIG: typ.Final[str] = (
    "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
    "is present, so what mutmut copies cannot be established"
)
_NO_SOURCE_PATHS: typ.Final[str] = (
    "the mutmut configuration declares neither source_paths nor the deprecated "
    "paths_to_mutate, so the copy set cannot be established; mutmut would guess it"
)


def _flattened_section(parser: ConfigParser) -> dict[str, object] | None:
    """Return the parsed ``[mutmut]`` section, or ``None`` when it is absent.

    Reading a value can raise ``InterpolationSyntaxError``, a ``ConfigError``,
    so the caller keeps this inside its ``try``.
    """
    if not parser.has_section("mutmut"):
        return None
    section = parser["mutmut"]
    table: dict[str, object] = {}
    for key in ("source_paths", "paths_to_mutate", "also_copy"):
        if key not in section:
            continue
        raw = section[key]
        # mutmut splits a multi-line value on newlines and wraps a single line
        # as one entry, so a comma-separated line is one path here too.
        table[key] = (
            [item for item in raw.split("\n") if item] if "\n" in raw else [raw]
        )
    return table


def _setup_cfg_table(root: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return ``setup.cfg``'s ``[mutmut]`` section, or why it went unread."""
    path = root / "setup.cfg"
    if not path.is_file():
        return None, None
    parser = ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        table = _flattened_section(parser)
    except (OSError, UnicodeDecodeError, ConfigError) as error:
        return None, f"{path} could not be read: {error}"
    return table, None


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
    table, problem = _setup_cfg_table(root)
    if table is not None:
        return table, None
    if problem is not None:
        # An unreadable setup.cfg is not the same fact as one without a
        # [mutmut] section, and saying the section is absent would mislead.
        return None, problem
    return None, _NO_CONFIG


def mutmut_copy_set(
    root: Path, version: str
) -> tuple[CopyContract | None, tuple[str, ...]]:
    """Return the copy contract for *version*, or why it is unknown.

    Parameters
    ----------
    root : Path
        The checkout holding the mutmut configuration.
    version : str
        The mutmut release the run selects. Its contract must be known: the
        default copy set changes between releases, so assuming one release's
        set while another runs would certify a sandbox the run never builds.

    Returns
    -------
    tuple[CopyContract | None, tuple[str, ...]]
        The contract when it can be established, or ``None`` with the findings
        that explain why it cannot.

    """
    known = copy_contract(version)
    if known is None:
        return None, (
            (
                f"the copy contract for mutmut {version or '(unspecified)'} is not "
                f"established; this helper knows {sorted(MUTMUT_COPY_CONTRACTS)}"
            ),
        )
    table, problem = _mutmut_table(root)
    if table is None:
        return None, (problem or "the mutmut configuration could not be read",)
    try:
        # mutmut reads ``paths_to_mutate`` when ``source_paths`` is empty,
        # warning that it is deprecated; the copy set is the same either way.
        source_paths = strings(
            table.get("source_paths"), "[tool.mutmut] source_paths"
        ) or strings(table.get("paths_to_mutate"), "[tool.mutmut] paths_to_mutate")
        also_copy = strings(table.get("also_copy"), "[tool.mutmut] also_copy")
    except DeclarationError as error:
        return None, (str(error),)
    if not source_paths:
        return None, (_NO_SOURCE_PATHS,)
    defaults, globs = known
    return CopyContract((*defaults, *source_paths, *also_copy), globs), ()
