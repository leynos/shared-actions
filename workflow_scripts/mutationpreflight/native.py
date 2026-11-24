"""Checking a locked native dependency's revision and interpreter support.

Support that cannot be established is reported as unknown, never assumed. The
ABI forward-compatibility escape hatch is recorded for diagnosis and is not
treated as proof that the interpreter is supported.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

from .constants import FAILED, SKIPPED, UNKNOWN
from .interpreters import judge
from .lockfile import (
    find_locked,
    pinned_revision,
    source_text,
)
from .models import Declaration, NativeRequirement, PhaseResult, phase_outcome

if typ.TYPE_CHECKING:
    from .ports import Run

_NO_NATIVE: typ.Final[str] = "no native dependency declared"

#: A hexadecimal commit id short enough to be a prefix rather than a complete
#: id. A declaration may name the full commit or any unambiguous prefix.
_COMMIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{4,40}")


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


@dc.dataclass(frozen=True, slots=True)
class LockIdentity:
    """The lock's record of one package, as one comparable value.

    Version, source and revision are compared together: a registry dependency
    records no revision, so a relock that moves its version would otherwise
    compare equal before and after.

    Attributes
    ----------
    version : str
        The locked version, empty when the record names none.
    source : str
        The record's source rendered as text, empty when it names none.
    revision : str
        The commit the record pins, empty when it records none.

    """

    version: str
    source: str
    revision: str

    def render(self) -> str:
        """Return the identity as the phrase a finding quotes."""
        return (
            f"version {self.version!r}, source {self.source!r}, "
            f"revision {self.revision!r}"
        )


def identity_of(package: dict[str, object]) -> LockIdentity:
    """Return the identity one lock record holds."""
    return LockIdentity(
        version=str(package.get("version", "")),
        source=source_text(package),
        revision=pinned_revision(package) or "",
    )


def lock_identity(
    run: Run, native: NativeRequirement | None
) -> tuple[LockIdentity | None, str]:
    """Return what the lock records for *native*, or why it cannot say.

    A lock that cannot be read, or that records no such package, yields no
    identity and the reason, which the caller records. A missing baseline must
    not read as a change when it is compared with a later reading, so the two
    are told apart rather than both being ``None``.

    Parameters
    ----------
    run : Run
        The checkout whose ``uv.lock`` is read, through its repository adapter.
    native : NativeRequirement | None
        The declared dependency, or ``None`` when the lane declares none.

    Returns
    -------
    tuple[LockIdentity | None, str]
        The lock's record of the package, and why it records none.

    """
    if native is None:
        return None, ""
    packages, problem = run.reader.read_lock_packages(run.root)
    if problem is not None:
        return None, problem
    locked = find_locked(packages, native.package)
    if locked is None:
        return None, f"uv.lock records no package named {native.package!r}"
    return identity_of(locked), ""


def judged_identity(judged: PhaseResult) -> LockIdentity | None:
    """Return the identity *judged* recorded, or ``None`` when it recorded none.

    Parameters
    ----------
    judged : PhaseResult
        A native judgement to read the lock's identity back from.

    Returns
    -------
    LockIdentity | None
        The identity the judgement recorded, or ``None`` when the package was
        absent from the lock or the lock could not be read.

    """
    if "locked_revision" not in judged.detail:
        return None
    return LockIdentity(
        version=str(judged.detail.get("locked_version", "")),
        source=str(judged.detail.get("locked_source", "")),
        revision=str(judged.detail["locked_revision"]),
    )


def _locked_identity(
    run: Run, native: NativeRequirement
) -> tuple[dict[str, object], list[str], list[str]]:
    """Return the lock's record of *native*, and what that leaves open."""
    packages, problem = run.reader.read_lock_packages(run.root)
    if problem is not None:
        return {}, [], [problem]
    locked = find_locked(packages, native.package)
    if locked is None:
        finding = f"the declared native dependency {native.package!r} is not in uv.lock"
        return {}, [finding], []
    identity = identity_of(locked)
    recorded: dict[str, object] = {
        "locked_version": identity.version,
        "locked_source": identity.source,
        "locked_revision": identity.revision,
    }
    finding = _revision_finding(native, locked)
    if finding is not None:
        return recorded, [finding], []
    return recorded, [], []


def _support_findings(
    native: NativeRequirement, run: Run
) -> tuple[list[str], list[str]]:
    """Return why the interpreter is out of range, or why support is unknown."""
    project_python = run.runtimes.project
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
    return judge(
        run.rule,
        run.rule_problem,
        native.supported_python,
        project_python,
        subject=f"{native.package}'s supported-python",
    )


def changed_lock_phase(
    judged: PhaseResult,
    native: NativeRequirement,
    before: LockIdentity,
    after: LockIdentity | None,
) -> PhaseResult:
    """Return *judged* failed by a declared check that rewrote the lock.

    The checks run in the checkout on the cargo lane, after the lock was read
    and before the mutation run starts. A check that relocks the project would
    leave the judged pin describing an environment the run no longer uses, so
    an identity that moved across the checks fails the native phase. A lock
    that no longer establishes the identity at all is a change too: the checks
    removed the very record the judgement had established.

    Parameters
    ----------
    judged : PhaseResult
        The native judgement made after the checks ran.
    native : NativeRequirement
        The declared dependency whose lock moved.
    before : LockIdentity
        What the lock recorded before the checks ran.
    after : LockIdentity | None
        What the lock records now, or ``None`` when it no longer establishes
        the package's identity.

    Returns
    -------
    PhaseResult
        The native phase, failed, naming the identity that moved and the
        remedy. The judgement's own findings are kept: a lock the checks moved
        does not excuse a revision or range that already did not hold.

    """
    if after is None:
        finding = (
            f"a declared check left uv.lock without an identity for "
            f"{native.package}; it recorded {before.render()} before the checks. "
            f"Run the checks with the lock enforced (uv's --locked or UV_LOCKED) "
            f"so they cannot rewrite it"
        )
    else:
        finding = (
            f"a declared check changed uv.lock for {native.package} from "
            f"{before.render()} to {after.render()}; run the checks with the lock "
            f"enforced (uv's --locked or UV_LOCKED) so they cannot relock the project"
        )
    blocking = [*judged.findings, finding] if judged.status == FAILED else [finding]
    indeterminate = list(judged.findings) if judged.status == UNKNOWN else []
    detail: dict[str, object] = {"before_checks": dc.asdict(before)}
    detail.update(judged.detail)
    detail["after_checks"] = None if after is None else dc.asdict(after)
    if native.probe:
        # A probe would run under a lock the checks have moved, so it is
        # withheld, and the envelope records that rather than silence.
        detail["probe"] = {"command": list(native.probe), "skipped": finding}
    return phase_outcome("native", blocking, indeterminate, detail)


def judge_native(run: Run, declaration: Declaration) -> PhaseResult:
    """Judge the declared native dependency's revision and interpreter range.

    The judgement is a query: it reads the lock and the declaration's range,
    and runs nothing, so it can be shown to leave the checkout untouched. The
    probe that exercises the built extension is composed onto its result by the
    caller.

    Parameters
    ----------
    run : Run
        The checkout whose ``uv.lock`` pins the dependency, the project
        interpreter and the recorded ABI3 setting.
    declaration : Declaration
        The repository's declaration, whose ``native`` table is judged.

    Returns
    -------
    PhaseResult
        ``skipped`` when nothing is declared, ``unknown`` when the lock or the
        support range could not establish compatibility, ``fail`` when a
        revision or a range does not hold, ``pass`` otherwise.

    """
    native = declaration.native
    if native is None:
        return PhaseResult("native", SKIPPED, (), {"reason": _NO_NATIVE})
    identity: dict[str, object] = {"package": native.package}
    locked_detail, lock_blocking, lock_unknown = _locked_identity(run, native)
    identity.update(locked_detail)
    identity["supported_python"] = native.supported_python
    identity["abi3_forward_compatibility"] = run.abi3
    support_blocking, support_unknown = _support_findings(native, run)
    return phase_outcome(
        "native",
        [*lock_blocking, *support_blocking],
        [*lock_unknown, *support_unknown],
        identity,
    )
