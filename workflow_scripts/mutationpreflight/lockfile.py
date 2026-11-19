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
    """Return ``uv.lock``'s package records, or why they could not be read."""
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
    """Return the locked record for *name*, comparing canonical names."""
    wanted = canonicalize_name(name)
    for package in packages:
        found = package.get("name")
        if isinstance(found, str) and canonicalize_name(found) == wanted:
            return package
    return None


def source_text(package: dict[str, object]) -> str:
    """Return the locked record's source as text, for identity reporting."""
    source = package.get("source", {})
    if isinstance(source, dict):
        entries = typ.cast("dict[str, object]", source)
        return " ".join(f"{key}={value}" for key, value in sorted(entries.items()))
    return str(source)


def git_revision(git: str) -> str | None:
    """Return the commit a uv git source records, or ``None`` when it names none.

    uv writes a git source as ``URL?rev=REV#REV``: the fragment is the commit,
    while the query may name a tag or a branch instead.
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
