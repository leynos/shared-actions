"""Checking a locked native dependency's revision and interpreter support.

Support that cannot be established is reported as unknown, never assumed. The
ABI forward-compatibility escape hatch is recorded for diagnosis and is not
treated as proof that the interpreter is supported.
"""

from __future__ import annotations

import os
import typing as typ

from packaging.utils import canonicalize_name

if typ.TYPE_CHECKING:
    from pathlib import Path

from .constants import MAX_CHECK_TIMEOUT_SECONDS, SKIPPED
from .interpreters import judge
from .models import Declaration, NativeRequirement, PhaseResult, Ran, phase_outcome
from .running import last_line, run_command
from .toml_documents import read_document

_NO_NATIVE: typ.Final[str] = "no native dependency declared"


def _locked_packages(repo_root: Path) -> tuple[list[dict[str, object]], str | None]:
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


def _find_locked(
    packages: list[dict[str, object]], name: str
) -> dict[str, object] | None:
    """Return the locked record for *name*, comparing canonical names."""
    wanted = canonicalize_name(name)
    for package in packages:
        found = package.get("name")
        if isinstance(found, str) and canonicalize_name(found) == wanted:
            return package
    return None


def _source_text(package: dict[str, object]) -> str:
    """Return the locked record's source as text, for identity reporting."""
    source = package.get("source", {})
    if isinstance(source, dict):
        entries = typ.cast("dict[str, object]", source)
        return " ".join(f"{key}={value}" for key, value in sorted(entries.items()))
    return str(source)


def abi3_escape_hatch() -> str:
    """Return the ABI3 compatibility setting, without reading it as proof.

    Returns
    -------
    str
        ``PYO3_USE_ABI3_FORWARD_COMPATIBILITY`` as the environment spells it,
        or ``"unset"``. The value is recorded for diagnosis only: setting it
        makes a build skip a version check rather than establish support.

    """
    return os.environ.get("PYO3_USE_ABI3_FORWARD_COMPATIBILITY", "") or "unset"


def _locked_identity(
    repo_root: Path, native: NativeRequirement
) -> tuple[dict[str, object], list[str], list[str]]:
    """Return the lock's record of *native*, and what that leaves open."""
    packages, problem = _locked_packages(repo_root)
    if problem is not None:
        return {}, [], [problem]
    locked = _find_locked(packages, native.package)
    if locked is None:
        finding = f"the declared native dependency {native.package!r} is not in uv.lock"
        return {}, [finding], []
    source = _source_text(locked)
    identity: dict[str, object] = {
        "locked_version": locked.get("version", ""),
        "locked_source": source,
    }
    if native.revision and native.revision not in source:
        finding = (
            f"uv.lock pins {native.package} at {source!r}, which is not the "
            f"declared revision {native.revision!r}"
        )
        return identity, [finding], []
    return identity, [], []


def _support_findings(
    native: NativeRequirement, project_python: str
) -> tuple[list[str], list[str]]:
    """Return why the interpreter is out of range, or why support is unknown."""
    if not native.supported_python:
        unknown = (
            f"no supported-python range is declared for {native.package}, so "
            f"interpreter compatibility is unknown rather than established"
        )
        return [], [unknown]
    if not project_python:
        unknown = (
            f"no project interpreter was supplied, so {native.package}'s "
            f"declared support {native.supported_python!r} was not checked"
        )
        return [], [unknown]
    violations = judge(
        native.supported_python,
        project_python,
        subject=f"{native.package}'s supported-python",
    )
    return violations, []


def _probe_detail(
    command: tuple[str, ...], outcome: Ran | None, problem: str
) -> dict[str, object]:
    """Return what a probe reported, bounded, for the envelope."""
    if outcome is None:
        return {"command": list(command), "problem": problem}
    return {
        "command": list(command),
        "exit_code": outcome.exit_code,
        "stderr": outcome.stderr,
        "stdout": outcome.stdout,
    }


def _probe_findings(
    native: NativeRequirement, repo_root: Path
) -> tuple[list[str], dict[str, object] | None]:
    """Run the declared probe, returning its finding and its evidence."""
    if not native.probe:
        return [], None
    outcome, problem = run_command(
        native.probe,
        cwd=repo_root,
        env={},
        timeout=MAX_CHECK_TIMEOUT_SECONDS,
    )
    evidence = _probe_detail(native.probe, outcome, problem)
    if outcome is None:
        finding = f"the native probe for {native.package} could not run: {problem}"
        return [finding], evidence
    if outcome.exit_code != 0:
        finding = (
            f"the native probe for {native.package} failed with exit code "
            f"{outcome.exit_code}: {last_line(outcome)}"
        )
        return [finding], evidence
    return [], evidence


def run_native_phase(
    repo_root: Path, declaration: Declaration, project_python: str
) -> PhaseResult:
    """Check the declared native dependency's revision and interpreter range.

    Parameters
    ----------
    repo_root : Path
        The checkout whose ``uv.lock`` pins the dependency.
    declaration : Declaration
        The repository's declaration, whose ``native`` table is checked.
    project_python : str
        The interpreter the caller selected for the project.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the lock or the
        support range could not establish compatibility, ``fail`` otherwise
        when a revision, a range or the probe does not hold.

    """
    native = declaration.native
    if native is None:
        return PhaseResult("native", SKIPPED, (), {"reason": _NO_NATIVE})
    identity: dict[str, object] = {"package": native.package}
    locked_detail, lock_blocking, lock_unknown = _locked_identity(repo_root, native)
    identity.update(locked_detail)
    identity["supported_python"] = native.supported_python
    identity["abi3_forward_compatibility"] = abi3_escape_hatch()
    support_blocking, support_unknown = _support_findings(native, project_python)
    probe_blocking, probe_detail = _probe_findings(native, repo_root)
    if probe_detail is not None:
        identity["probe"] = probe_detail
    return phase_outcome(
        "native",
        [*lock_blocking, *support_blocking, *probe_blocking],
        [*lock_unknown, *support_unknown],
        identity,
    )
