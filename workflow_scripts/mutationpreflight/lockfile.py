"""Reading what ``uv.lock`` pins, as a declaration must be judged against it.

The lock is a document, not a text: a revision is read from the field that
records it, so a commit id that merely appears in a repository URL is not
mistaken for a pin. Reading the document is the repository adapter's business;
this module judges the records it returns.
"""

from __future__ import annotations

import typing as typ

from packaging.utils import canonicalize_name


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
