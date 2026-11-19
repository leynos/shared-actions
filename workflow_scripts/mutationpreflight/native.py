"""Checking a locked native dependency's revision and interpreter support.

Support that cannot be established is reported as unknown, never assumed. The
ABI forward-compatibility escape hatch is recorded for diagnosis and is not
treated as proof that the interpreter is supported.
"""

from __future__ import annotations

import os
import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

from .constants import MAX_CHECK_TIMEOUT_SECONDS, SKIPPED
from .interpreters import judge
from .lockfile import (
    find_locked,
    locked_packages,
    pinned_revision,
    source_text,
)
from .models import Declaration, NativeRequirement, PhaseResult, Ran, phase_outcome
from .running import first_diagnostic, run_command

_NO_NATIVE: typ.Final[str] = "no native dependency declared"

#: A hexadecimal commit id short enough to be a prefix rather than a complete
#: id. A declaration may name the full commit or any unambiguous prefix.
_COMMIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{4,40}")

#: Why a probe was not run: its verdict would describe an environment the
#: mutation run is not going to use.
_PROBE_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so {package}'s probe was not run: it would "
    "sync the project under the interpreter the phase rejected"
)


def _revision_holds(declared: str, pinned: str) -> bool:
    """Return whether *pinned* is the revision *declared* names."""
    if pinned == declared:
        return True
    return bool(
        _COMMIT_PREFIX.fullmatch(declared)
        and pinned.lower().startswith(declared.lower())
    )


def _revision_finding(
    native: NativeRequirement, package: dict[str, object]
) -> str | None:
    """Return why the lock does not pin the declared revision, or ``None``."""
    if not native.revision:
        return None
    pinned = pinned_revision(package)
    if pinned is None:
        source = source_text(package)
        return (
            f"uv.lock records no pinned revision for {native.package} at "
            f"{source!r}, so the declared revision {native.revision!r} is not "
            f"established"
        )
    if _revision_holds(native.revision, pinned):
        return None
    return (
        f"uv.lock pins {native.package} at revision {pinned!r}, which is not the "
        f"declared revision {native.revision!r}"
    )


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
    packages, problem = locked_packages(repo_root)
    if problem is not None:
        return {}, [], [problem]
    locked = find_locked(packages, native.package)
    if locked is None:
        finding = f"the declared native dependency {native.package!r} is not in uv.lock"
        return {}, [finding], []
    pinned = pinned_revision(locked)
    identity: dict[str, object] = {
        "locked_version": locked.get("version", ""),
        "locked_source": source_text(locked),
        "locked_revision": pinned or "",
    }
    finding = _revision_finding(native, locked)
    if finding is not None:
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
    violations, problem = judge(
        native.supported_python,
        project_python,
        subject=f"{native.package}'s supported-python",
    )
    if problem:
        # The shared rule judged nothing, so support stays unknown rather than
        # failing an interpreter against a range it never read.
        return violations, [problem]
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
    native: NativeRequirement, repo_root: Path, *, execute: bool
) -> tuple[list[str], list[str], dict[str, object] | None]:
    """Run the declared probe, returning its blocking, unknown and evidence.

    *execute* is false when an earlier prerequisite failed. The probe is then
    recorded as skipped rather than run: a command that syncs the project
    would otherwise act under the interpreter the preflight just refused, and
    its verdict would describe an environment the mutation run will not use.
    """
    if not native.probe:
        return [], [], None
    if not execute:
        blocked = _PROBE_BLOCKED.format(package=native.package)
        return [], [blocked], {"command": list(native.probe), "skipped": blocked}
    outcome, problem = run_command(
        native.probe,
        cwd=repo_root,
        env={},
        timeout=MAX_CHECK_TIMEOUT_SECONDS,
    )
    evidence = _probe_detail(native.probe, outcome, problem)
    if outcome is None:
        finding = f"the native probe for {native.package} could not run: {problem}"
        return [finding], [], evidence
    if outcome.exit_code != 0:
        finding = (
            f"the native probe for {native.package} failed with exit code "
            f"{outcome.exit_code}: {first_diagnostic(outcome)}"
        )
        return [finding], [], evidence
    return [], [], evidence


def run_native_phase(
    repo_root: Path,
    declaration: Declaration,
    project_python: str,
    *,
    execute_probe: bool = True,
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
    execute_probe : bool
        Whether the declared probe may run. False when an earlier prerequisite
        failed, so the probe is recorded as skipped instead of acting under the
        environment that prerequisite rejected.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the lock, the
        support range or a skipped probe could not establish compatibility,
        ``fail`` otherwise when a revision, a range or the probe does not hold.

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
    probe_blocking, probe_unknown, probe_detail = _probe_findings(
        native, repo_root, execute=execute_probe
    )
    if probe_detail is not None:
        identity["probe"] = probe_detail
    return phase_outcome(
        "native",
        [*lock_blocking, *support_blocking, *probe_blocking],
        [*lock_unknown, *support_unknown, *probe_unknown],
        identity,
    )
