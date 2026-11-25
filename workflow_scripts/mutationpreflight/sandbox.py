"""Comparing declared sandbox paths with what mutmut will actually copy.

The comparison is against the copy set, not against the checkout: a path that
exists but is never copied fails the baseline inside ``mutants/``, which is the
failure this phase exists to find before any mutant is enumerated.
"""

from __future__ import annotations

import typing as typ
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .constants import SKIPPED
from .copy_set import CopyContract, copy_entry_problems, mutmut_copy_set
from .models import Declaration, PhaseResult, phase_outcome
from .schema import excluded_problem, path_problem

if typ.TYPE_CHECKING:
    from .ports import ConfigurationReader, LocatedPath, PathLocator, Settings

_NO_SANDBOX: typ.Final[str] = "no sandbox_paths declared"


def _resolved_problem(located: LocatedPath) -> str | None:
    """Return why what a declared path resolves to cannot be a sandbox path."""
    if not located.inside_root:
        return (
            f"resolves to {located.resolved!r} outside the checkout, so the "
            f"sandbox would carry something the declaration did not name"
        )
    return excluded_problem(located.parts, "resolves into")


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
    paths: PathLocator, root: Path, declared: str, contract: CopyContract | None
) -> tuple[str | None, str | None]:
    """Return one path's finding, and the envelope category it belongs to.

    The first usable answer wins: a syntactic problem outranks an absence,
    which outranks a symlink leaving the checkout, which outranks the copy
    question. *contract* is ``None`` when that last question cannot be asked.
    """
    problem = path_problem(declared)
    if problem is not None:
        return f"sandbox path {declared!r} {problem}", None
    located, resolution = paths.locate(root, declared)
    if located is None:
        return (
            f"sandbox path {declared!r} could not be resolved in the checkout: "
            f"{resolution}"
        ), None
    if not located.exists:
        finding = (
            f"sandbox path {declared!r} is not in the checkout, so the "
            f"declaration names a requirement nothing satisfies"
        )
        return finding, "missing_in_checkout"
    resolved = _resolved_problem(located)
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
    paths: PathLocator,
    root: Path,
    declared_paths: tuple[str, ...],
    contract: CopyContract | None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Return every finding, with the paths classified for the envelope.

    *contract* is ``None`` when mutmut's copy set could not be established:
    the syntax and existence of each path are still checked, because neither
    depends on knowing what mutmut would copy.
    """
    findings: list[str] = []
    categories: dict[str, list[str]] = {"missing_in_checkout": [], "not_copied": []}
    for declared in declared_paths:
        finding, category = _path_finding(paths, root, declared, contract)
        if finding is not None:
            findings.append(finding)
        if category is not None:
            categories[category].append(declared)
    return findings, categories


def run_sandbox_phase(
    configuration: ConfigurationReader,
    paths: PathLocator,
    settings: Settings,
    declaration: Declaration,
) -> PhaseResult:
    """Compare the declared sandbox requirements with what mutmut will copy.

    Parameters
    ----------
    configuration : ConfigurationReader
        How the mutmut configuration the copy set comes from is read.
    paths : PathLocator
        How each declared path is located in the checkout.
    settings : Settings
        The checkout the declaration belongs to, and the mutmut release whose
        copy contract the comparison uses.
    declaration : Declaration
        The repository's declaration, whose ``sandbox_paths`` are checked.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when mutmut's copy
        contract cannot be established, ``fail`` when a declared path is
        unusable or would not survive into ``mutants/``.

    """
    if not declaration.sandbox_paths:
        return PhaseResult("sandbox", SKIPPED, (), {"reason": _NO_SANDBOX})
    table, problem = configuration.read_mutmut_configuration(settings.root)
    detail: dict[str, object] = {
        "declared": list(declaration.sandbox_paths),
        "mutmut_version": settings.mutmut_version,
        "copied": [],
    }
    invalid = copy_entry_problems(table)
    if invalid:
        # An entry that escapes the checkout is a declaration this run cannot
        # honour, not a copy set it merely cannot establish: it fails.
        return phase_outcome("sandbox", list(invalid), [], detail)
    contract, problems = mutmut_copy_set(settings.mutmut_version, table, problem)
    detail["copied"] = [] if contract is None else sorted(contract.paths)
    findings, classified = _review_paths(
        paths, settings.root, declaration.sandbox_paths, contract
    )
    detail.update(classified)
    if contract is None:
        # An unusable or absent path is a failure whether or not the copy set
        # could be established; only the copy question itself stays unknown.
        return phase_outcome("sandbox", findings, list(problems), detail)
    return phase_outcome("sandbox", findings, [], detail)
