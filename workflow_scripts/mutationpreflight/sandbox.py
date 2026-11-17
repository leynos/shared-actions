"""Comparing declared sandbox paths with what mutmut will actually copy.

The comparison is against the copy set, not against the checkout: a path that
exists but is never copied fails the baseline inside ``mutants/``, which is the
failure this phase exists to find before any mutant is enumerated.
"""

from __future__ import annotations

import typing as typ
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .constants import (
    EXCLUDED_DIRECTORIES,
    MUTMUT_DEFAULT_COPIES,
    MUTMUT_DEFAULT_GLOBS,
    SKIPPED,
)
from .copy_set import mutmut_copy_set
from .models import Declaration, PhaseResult, phase_outcome

_NO_SANDBOX: typ.Final[str] = "no sandbox_paths declared"


def path_problem(declared: str) -> str | None:
    """Return why *declared* cannot be a sandbox path, or ``None`` when it can.

    Parameters
    ----------
    declared : str
        The path as the declaration spells it.

    Returns
    -------
    str | None
        Why the path is unusable, or ``None`` when it stays inside the
        checkout and outside build output and caches.

    """
    if not declared.strip():
        return "is empty"
    path = PurePosixPath(declared)
    if path.is_absolute():
        return "is absolute, but sandbox paths are relative to the checkout"
    parts = [part for part in path.parts if part not in ("", ".")]
    if ".." in parts:
        return "escapes the checkout"
    for part in parts:
        if part in EXCLUDED_DIRECTORIES:
            return (
                f"is under {part!r}, which is build output, a tool cache or the "
                f"version-control store; the sandbox must not carry it"
            )
    return None


def _covers(entry: str, wanted: str) -> bool:
    """Return whether copying *entry* materialises *wanted*."""
    entry_path = PurePosixPath(entry.rstrip("/"))
    wanted_path = PurePosixPath(wanted.rstrip("/"))
    return entry_path == wanted_path or entry_path in wanted_path.parents


def _copied_by_default_glob(wanted: str) -> bool:
    """Return whether one of mutmut's root-level default globs matches *wanted*."""
    path = PurePosixPath(wanted)
    return path.parent == PurePosixPath(".") and any(
        fnmatch(path.name, pattern) for pattern in MUTMUT_DEFAULT_GLOBS
    )


def _is_copied(declared: str, copied: tuple[str, ...]) -> bool:
    """Return whether mutmut's copy set materialises *declared*."""
    if _copied_by_default_glob(declared):
        return True
    return any(_covers(entry, declared) for entry in copied)


def _review_paths(
    repo_root: Path, declared_paths: tuple[str, ...], copied: tuple[str, ...] | None
) -> tuple[list[str], dict[str, object]]:
    """Return every finding, with the paths classified for the envelope.

    *copied* is ``None`` when mutmut's copy set could not be established: the
    syntax and existence of each path are still checked, because neither
    depends on knowing what mutmut would copy.
    """
    findings: list[str] = []
    missing: list[str] = []
    not_copied: list[str] = []
    for declared in declared_paths:
        problem = path_problem(declared)
        if problem is not None:
            findings.append(f"sandbox path {declared!r} {problem}")
            continue
        if not (repo_root / declared).exists():
            missing.append(declared)
            findings.append(
                f"sandbox path {declared!r} is not in the checkout, so the "
                f"declaration names a requirement nothing satisfies"
            )
            continue
        if copied is None or _is_copied(declared, copied):
            continue
        not_copied.append(declared)
        findings.append(
            f"sandbox path {declared!r} is not materialised in mutants/: add it "
            f"to [tool.mutmut] also_copy, because mutmut copies only "
            f"{list(MUTMUT_DEFAULT_COPIES)} and root {list(MUTMUT_DEFAULT_GLOBS)} "
            f"besides source_paths and also_copy"
        )
    return findings, {"missing_in_checkout": missing, "not_copied": not_copied}


def run_sandbox_phase(repo_root: Path, declaration: Declaration) -> PhaseResult:
    """Compare the declared sandbox requirements with what mutmut will copy.

    Parameters
    ----------
    repo_root : Path
        The checkout the declaration belongs to.
    declaration : Declaration
        The repository's declaration, whose ``sandbox_paths`` are checked.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when mutmut's copy
        set cannot be established, ``fail`` when a declared path is unusable
        or would not survive into ``mutants/``.

    """
    if not declaration.sandbox_paths:
        return PhaseResult("sandbox", SKIPPED, (), {"reason": _NO_SANDBOX})
    copied, problems = mutmut_copy_set(repo_root)
    detail: dict[str, object] = {
        "declared": list(declaration.sandbox_paths),
        "copied": [] if copied is None else sorted(copied),
    }
    findings, classified = _review_paths(repo_root, declaration.sandbox_paths, copied)
    detail.update(classified)
    if copied is None:
        # An unusable or absent path is a failure whether or not the copy set
        # could be established; only the copy question itself stays unknown.
        return phase_outcome("sandbox", findings, problems, detail)
    return phase_outcome("sandbox", findings, [], detail)
