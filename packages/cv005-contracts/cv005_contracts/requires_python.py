"""Hold the configured interpreter inside the project's `requires-python`.

`coverage.interpreter` pins every generator to the `interpreter` a repository
configures. If the project's own `pyproject.toml` declares a `requires-python`
that excludes it, `uv sync` refuses the interpreter and the coverage step
fails, which is the regression create-labels #114 and docx-comment-extractor
#44 hit when a resolver picked 3.13 against `>=3.14`. This module reads the
declaration, when there is one, and reports the mismatch.
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
    return _judge(requirement, interpreter)


def _judge(requirement: object, interpreter: str) -> list[str]:
    """Compare the interpreter with one `requires-python` value."""
    if not isinstance(requirement, str):
        return [f"requires-python must be a string, not {requirement!r}"]
    try:
        accepted = SpecifierSet(requirement)
        candidates = _candidates(interpreter, accepted)
    except (InvalidSpecifier, InvalidVersion, ValueError) as error:
        message = (
            f"requires-python {requirement!r} or interpreter {interpreter!r} "
            f"is invalid: {error}"
        )
        return [message]
    if any(accepted.contains(version, prereleases=True) for version in candidates):
        return []
    message = (
        f"the configured interpreter {interpreter!r} is outside the project's "
        f"requires-python {requirement!r}, so `uv sync` would refuse it"
    )
    return [message]


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
