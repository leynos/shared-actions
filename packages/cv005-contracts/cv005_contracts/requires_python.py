"""Hold the configured interpreter inside the project's `requires-python`.

`coverage.interpreter` pins every generator to the `interpreter` a repository
configures. If the project's own `pyproject.toml` declares a `requires-python`
that excludes it, `uv sync` refuses the interpreter and the coverage step
fails, which is the regression create-labels #114 and docx-comment-extractor
#44 hit when a resolver picked 3.13 against `>=3.14`. This module reads the
declaration, when there is one, and reports the mismatch.

The judging itself is `requirement_violations`, which asks one question of one
PEP 508 string and one interpreter. The coverage clause asks it of the
project's declaration; the mutation preflight asks it of a locked native
dependency's supported range. Sharing the function keeps the awkward case —
a bare `X.Y` interpreter, which uv may resolve to any patch — decided in one
place rather than twice.
"""

from __future__ import annotations

import tomllib
import typing as typ

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Patch numbers always tried for a bare `X.Y`, besides those next to a bound
#: the specifier names.
BASE_PATCHES: typ.Final[tuple[int, ...]] = (0, 1, 999)


def requires_python_violations(repo_root: Path, interpreter: str) -> list[str]:
    """Return why the interpreter falls outside the project's `requires-python`.

    Parameters
    ----------
    repo_root : Path
        The repository root, whose `pyproject.toml` is read when present.
    interpreter : str
        The configured interpreter, such as `3.13` or `3.13.5`.

    Returns
    -------
    list[str]
        One violation when the interpreter is not accepted, or when the
        declaration cannot be read; empty when there is no `pyproject.toml`
        or it declares no `requires-python`.

    Examples
    --------
    >>> from pathlib import Path
    >>> requires_python_violations(Path("/nonexistent"), "3.13")
    []

    """
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        declared = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return [f"pyproject.toml could not be read: {error}"]
    project = declared.get("project")
    requirement = project.get("requires-python") if isinstance(project, dict) else None
    if requirement is None:
        return []
    return requirement_violations(
        requirement, interpreter, subject="the project's requires-python"
    )


def requirement_violations(
    requirement: object, interpreter: str, *, subject: str
) -> list[str]:
    """Return why the interpreter falls outside one PEP 508 requirement.

    `requires_python_violations` is this function with the subject named as
    the project's own declaration. A caller with a different requirement to
    enforce — the mutation preflight checking a locked native dependency's
    supported range — passes its own subject rather than repeating the
    judging, so the bare-`X.Y` rule below cannot drift between them.

    Parameters
    ----------
    requirement : object
        The requirement string as declared; any other type is refused.
    interpreter : str
        The interpreter, such as `3.13` or `3.13.5`.
    subject : str
        What the requirement constrains, named in every finding.

    Returns
    -------
    list[str]
        One finding when the interpreter is not accepted, or when either
        side cannot be read; empty when it is accepted.

    Examples
    --------
    >>> requirement_violations(">=3.14", "3.13", subject="tei-rapporteur")
    ["the interpreter '3.13' is outside tei-rapporteur '>=3.14'"]
    >>> requirement_violations(">=3.12,<3.14", "3.13", subject="the project")
    []

    """
    if not isinstance(requirement, str):
        return [f"{subject} must be a string, not {requirement!r}"]
    try:
        accepted = SpecifierSet(requirement)
        candidates = _candidates(interpreter, accepted)
    except (InvalidSpecifier, InvalidVersion, ValueError) as error:
        message = (
            f"{subject} {requirement!r} or interpreter {interpreter!r} "
            f"is invalid: {error}"
        )
        return [message]
    if any(accepted.contains(version, prereleases=True) for version in candidates):
        return []
    return [f"the interpreter {interpreter!r} is outside {subject} {requirement!r}"]


def _candidates(interpreter: str, accepted: SpecifierSet) -> list[Version]:
    """Return the versions an interpreter request can resolve to.

    An `X.Y.Z` is one version. A bare `X.Y` resolves to whichever patch uv
    finds, so it is accepted if any patch is; that is decided by trying the
    patches next to every bound the specifier names in that minor, since a
    range's membership can only change at a bound.
    """
    version = Version(interpreter)
    if len(version.release) < 2:
        message = f"{interpreter!r} names no minor version"
        raise ValueError(message)
    if len(version.release) >= 3:
        return [version]
    major, minor = version.release[0], version.release[1]
    patches = set(BASE_PATCHES)
    for bound in _bounds(accepted, major, minor):
        patches |= {max(bound - 1, 0), bound, bound + 1}
    return [Version(f"{major}.{minor}.{patch}") for patch in sorted(patches)]


def _bounds(accepted: SpecifierSet, major: int, minor: int) -> list[int]:
    """Return the patch numbers the specifier names within one minor."""
    found: list[int] = []
    for spec in accepted:
        release = _release(spec.version)
        if release[:2] == (major, minor) and len(release) >= 3:
            found.append(release[2])
    return found


def _release(text: str) -> tuple[int, ...]:
    """Return a specifier's version as a release tuple, ignoring a `.*` suffix."""
    try:
        return Version(text.removesuffix(".*")).release
    except InvalidVersion:
        return ()
