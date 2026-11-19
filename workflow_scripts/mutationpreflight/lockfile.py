"""Reading what ``uv.lock`` pins, as a declaration must be judged against it.

The lock is a document, not a text: a revision is read from the field that
records it, so a commit id that merely appears in a repository URL is not
mistaken for a pin.
"""

from __future__ import annotations

import typing as typ

from packaging.utils import canonicalize_name

from .toml_documents import read_document

if typ.TYPE_CHECKING:
    from pathlib import Path


def locked_packages(
    repo_root: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Return ``uv.lock``'s package records, or why they could not be read.

    Parameters
    ----------
    repo_root : Path
        The checkout whose ``uv.lock`` is read.

    Returns
    -------
    tuple[list[dict[str, object]], str | None]
        Every package record the lock declares, and why none could be read. An
        unreadable lock is returned as a problem rather than an empty list, so
        the native phase reports the lock as unavailable instead of reporting
        every package as absent from it. A record that is not a table is
        skipped: the lock's own shape has already been refused if it is not an
        array.
    """
    lock = repo_root / "uv.lock"
    if not lock.is_file():
        return [], "uv.lock is absent, so no locked revision can be established"
    document, problem = read_document(lock)
    if problem is not None:
        return [], problem
    packages = (document or {}).get("package", [])
    if not isinstance(packages, list):
        return [], "uv.lock's package entry is not an array"
    records = typ.cast("list[object]", packages)
    tables = [item for item in records if isinstance(item, dict)]
    return [typ.cast("dict[str, object]", item) for item in tables], None


def find_locked(
    packages: list[dict[str, object]], name: str
) -> dict[str, object] | None:
    """Return the locked record for *name*, comparing canonical names.

    Parameters
    ----------
    packages : list[dict[str, object]]
        The records ``locked_packages`` returned.
    name : str
        The distribution name as the declaration spells it. Names are
        compared canonically, so ``Tei_Rapporteur`` finds ``tei-rapporteur``.

    Returns
    -------
    dict[str, object] | None
        The record the lock holds for *name*, or ``None`` when it holds none.
        A missing record is not the same finding as a missing pin: one says
        the dependency is not locked at all, the other that it is locked
        without recording the revision asked for.
    """
    wanted = canonicalize_name(name)
    for package in packages:
        found = package.get("name")
        if isinstance(found, str) and canonicalize_name(found) == wanted:
            return package
    return None


def source_text(package: dict[str, object]) -> str:
    """Return the locked record's source as text, for identity reporting.

    This is display text, not a pin: it names where the dependency came from,
    while ``pinned_revision`` reads the fields that record an exact commit.

    Parameters
    ----------
    package : dict[str, object]
        One record from ``uv.lock``.

    Returns
    -------
    str
        The source table rendered as ``key=value`` pairs, or the source's own
        text when it is not a table. Empty when the record names no source.
    """
    source = package.get("source", {})
    if isinstance(source, dict):
        entries = typ.cast("dict[str, object]", source)
        return " ".join(f"{key}={value}" for key, value in sorted(entries.items()))
    return str(source)


def git_revision(git: str) -> str | None:
    """Return the commit a uv git source records, or ``None`` when it names none.

    uv writes a git source as ``URL?rev=REV#REV``: the fragment is the commit,
    while the query may name a tag or a branch instead.

    Parameters
    ----------
    git : str
        The lock's ``git`` source string.

    Returns
    -------
    str | None
        The commit the source records, or ``None`` when it records none. A
        tag or branch in the query is not returned as a commit: only an
        explicit ``rev`` counts as a pin.
    """
    _, separator, fragment = git.partition("#")
    if separator and fragment:
        return fragment
    for entry in git.partition("?")[2].split("&"):
        key, _, value = entry.partition("=")
        if key == "rev" and value:
            return value
    return None


def pinned_revision(package: dict[str, object]) -> str | None:
    """Return the revision ``uv.lock`` pins, or ``None`` when it records none.

    The revision is read from the fields that record it, never searched for in
    the source text: a commit id that merely appears in a repository URL has
    not been pinned.

    Parameters
    ----------
    package : dict[str, object]
        One record from ``uv.lock``.

    Returns
    -------
    str | None
        The recorded ``rev``, else the commit named by the record's git
        source, else ``None``. ``None`` means the record does not establish a
        revision, which the native phase reports as an unproven pin rather
        than a mismatch.
    """
    source = package.get("source")
    if not isinstance(source, dict):
        return None
    entries = typ.cast("dict[str, object]", source)
    recorded = entries.get("rev")
    if isinstance(recorded, str) and recorded:
        return recorded
    git = entries.get("git")
    if not isinstance(git, str) or not git:
        return None
    return git_revision(git)
