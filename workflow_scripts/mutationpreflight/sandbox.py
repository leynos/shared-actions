"""Comparing declared sandbox paths with what mutmut will actually copy.

The comparison is against the copy set, not against the checkout: a path that
exists but is never copied fails the baseline inside ``mutants/``, which is the
failure this phase exists to find before any mutant is enumerated.
"""

from __future__ import annotations

import typing as typ
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .constants import EXCLUDED_DIRECTORIES, SKIPPED
from .copy_set import CopyContract, mutmut_copy_set
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
    return _excluded_problem(parts, "is under")


def _excluded_problem(parts: typ.Iterable[str], phrase: str) -> str | None:
    """Return why *parts* names build output, a tool cache or the VCS store.

    *phrase* opens the finding, so the same rule can describe a declared path
    and the path a symlink resolves to.
    """
    for part in parts:
        if part in EXCLUDED_DIRECTORIES:
            return (
                f"{phrase} {part!r}, which is build output, a tool cache or the "
                f"version-control store; the sandbox must not carry it"
            )
    return None


def _resolved_problem(repo_root: Path, declared: str) -> str | None:
    """Return why what *declared* resolves to cannot be a sandbox path."""
    root = repo_root.resolve()
    resolved = (root / declared).resolve()
    if not resolved.is_relative_to(root):
        return (
            f"resolves to {str(resolved)!r} outside the checkout, so the sandbox "
            f"would carry something the declaration did not name"
        )
    return _excluded_problem(resolved.relative_to(root).parts, "resolves into")


def _covers(entry: str, wanted: str) -> bool:
    """Return whether copying *entry* materializes *wanted*."""
    entry_path = PurePosixPath(entry.rstrip("/"))
    wanted_path = PurePosixPath(wanted.rstrip("/"))
    return entry_path == wanted_path or entry_path in wanted_path.parents


def _copied_by_default_glob(wanted: str, globs: tuple[str, ...]) -> bool:
    """Return whether a root-level glob of the selected release matches *wanted*."""
    path = PurePosixPath(wanted)
    return path.parent == PurePosixPath(".") and any(
        fnmatch(path.name, pattern) for pattern in globs
    )


def _is_copied(declared: str, contract: CopyContract) -> bool:
    """Return whether the selected release's contract materializes *declared*."""
    if _copied_by_default_glob(declared, contract.globs):
        return True
    return any(_covers(entry, declared) for entry in contract.paths)


def _path_finding(
    repo_root: Path, declared: str, contract: CopyContract | None
) -> tuple[str | None, str | None]:
    """Return one path's finding, and the envelope category it belongs to.

    The first usable answer wins: a syntactic problem outranks an absence,
    which outranks a symlink leaving the checkout, which outranks the copy
    question. *contract* is ``None`` when that last question cannot be asked.
    """
    problem = path_problem(declared)
    if problem is not None:
        return f"sandbox path {declared!r} {problem}", None
    if not (repo_root / declared).exists():
        finding = (
            f"sandbox path {declared!r} is not in the checkout, so the "
            f"declaration names a requirement nothing satisfies"
        )
        return finding, "missing_in_checkout"
    resolved = _resolved_problem(repo_root, declared)
    if resolved is not None:
        return f"sandbox path {declared!r} {resolved}", None
    if contract is None or _is_copied(declared, contract):
        return None, None
    finding = (
        f"sandbox path {declared!r} is not materialized in mutants/: add it "
        f"to [tool.mutmut] also_copy, because the selected mutmut copies only "
        f"{list(contract.paths)} and root {list(contract.globs)}"
    )
    return finding, "not_copied"


def _review_paths(
    repo_root: Path, declared_paths: tuple[str, ...], contract: CopyContract | None
) -> tuple[list[str], dict[str, list[str]]]:
    """Return every finding, with the paths classified for the envelope.

    *contract* is ``None`` when mutmut's copy set could not be established:
    the syntax and existence of each path are still checked, because neither
    depends on knowing what mutmut would copy.
    """
    findings: list[str] = []
    categories: dict[str, list[str]] = {"missing_in_checkout": [], "not_copied": []}
    for declared in declared_paths:
        finding, category = _path_finding(repo_root, declared, contract)
        if finding is not None:
            findings.append(finding)
        if category is not None:
            categories[category].append(declared)
    return findings, categories


def run_sandbox_phase(
    repo_root: Path, declaration: Declaration, mutmut_version: str
) -> PhaseResult:
    """Compare the declared sandbox requirements with what mutmut will copy.

    Parameters
    ----------
    repo_root : Path
        The checkout the declaration belongs to.
    declaration : Declaration
        The repository's declaration, whose ``sandbox_paths`` are checked.
    mutmut_version : str
        The mutmut release the run selects. The comparison uses that release's
        copy contract, never a different release's.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when mutmut's copy
        contract cannot be established, ``fail`` when a declared path is
        unusable or would not survive into ``mutants/``.

    """
    if not declaration.sandbox_paths:
        return PhaseResult("sandbox", SKIPPED, (), {"reason": _NO_SANDBOX})
    contract, problems = mutmut_copy_set(repo_root, mutmut_version)
    detail: dict[str, object] = {
        "declared": list(declaration.sandbox_paths),
        "mutmut_version": mutmut_version,
        "copied": [] if contract is None else sorted(contract.paths),
    }
    findings, classified = _review_paths(repo_root, declaration.sandbox_paths, contract)
    detail.update(classified)
    if contract is None:
        # An unusable or absent path is a failure whether or not the copy set
        # could be established; only the copy question itself stays unknown.
        return phase_outcome("sandbox", findings, list(problems), detail)
    return phase_outcome("sandbox", findings, [], detail)
