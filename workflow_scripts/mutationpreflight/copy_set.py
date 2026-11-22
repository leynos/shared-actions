"""Establishing which paths mutmut's sandbox will contain.

mutmut copies a fixed set of root files and globs, then everything named in
``source_paths`` and ``also_copy``. The configuration the run will use is read
by the repository adapter; this module judges whether the table it returns
describes a copy set this helper can establish.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

from .constants import MUTMUT_COPY_CONTRACTS
from .models import DeclarationError
from .schema import path_problem, strings

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


_NO_SOURCE_PATHS: typ.Final[str] = (
    "the mutmut configuration declares neither source_paths nor the deprecated "
    "paths_to_mutate, so the copy set cannot be established; mutmut would guess it"
)


def _declared_entries(
    table: dict[str, object],
) -> tuple[tuple[str, ...], str | None]:
    """Return the entries a declaration adds to mutmut's copy set, or why not."""
    try:
        # mutmut reads ``paths_to_mutate`` when ``source_paths`` is empty,
        # warning that it is deprecated; the copy set is the same either way.
        source_paths = strings(
            table.get("source_paths"), "[tool.mutmut] source_paths"
        ) or strings(table.get("paths_to_mutate"), "[tool.mutmut] paths_to_mutate")
        also_copy = strings(table.get("also_copy"), "[tool.mutmut] also_copy")
    except DeclarationError as error:
        return (), str(error)
    if not source_paths:
        return (), _NO_SOURCE_PATHS
    return (*source_paths, *also_copy), None


def _entry_findings(entries: typ.Iterable[str]) -> tuple[str, ...]:
    """Return a finding for each copy entry that must not be copied."""
    return tuple(
        f"[tool.mutmut] entry {entry!r} {problem}"
        for entry in entries
        if (problem := path_problem(entry)) is not None
    )


def copy_entry_problems(table: dict[str, object] | None) -> tuple[str, ...]:
    """Return findings for declared copy entries that must not be copied.

    Parameters
    ----------
    table : dict[str, object] | None
        The mutmut configuration the repository adapter read, or ``None`` when
        it could not be established.

    Returns
    -------
    tuple[str, ...]
        One finding per absolute, escaping or excluded entry the declaration
        adds to the copy set. Such an entry would copy something the checkout
        does not contain where the sandbox is meant to hold what it does, so a
        phase refuses it before any copy starts. A configuration whose entries
        cannot be read at all yields no findings here: ``mutmut_copy_set``
        reports that state, which stays unknown rather than reading as a bad
        entry.

    """
    if table is None:
        return ()
    entries, problem = _declared_entries(table)
    if problem is not None:
        return ()
    return _entry_findings(entries)


def mutmut_copy_set(
    version: str, table: dict[str, object] | None, problem: str | None
) -> tuple[CopyContract | None, tuple[str, ...]]:
    """Return the copy contract for *version*, or why it is unknown.

    Parameters
    ----------
    version : str
        The mutmut release the run selects. Its contract must be known: the
        default copy set changes between releases, so assuming one release's
        set while another runs would certify a sandbox the run never builds.
    table : dict[str, object] | None
        The mutmut configuration the repository adapter read, or ``None`` when
        it could not be established.
    problem : str | None
        Why *table* is ``None``, as the adapter named it.

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
    if table is None:
        return None, (problem or "the mutmut configuration could not be read",)
    entries, entry_problem = _declared_entries(table)
    if entry_problem is not None:
        return None, (entry_problem,)
    # The defaults are this helper's own constants; the declared entries are
    # the caller's, and an absolute or escaping one would write outside the
    # sandbox the checks are meant to run in.
    escapes = _entry_findings(entries)
    if escapes:
        return None, escapes
    defaults, globs = known
    return CopyContract((*defaults, *entries), globs), ()
