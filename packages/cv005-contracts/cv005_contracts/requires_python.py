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

#: Patch releases tried for a bare `X.Y` interpreter, which uv resolves to
#: whichever patch it finds. A range such as `>=3.13.1` or `<3.13.2` admits
#: some of them, and the interpreter is accepted if any is admitted.
PATCHES: typ.Final[tuple[int, ...]] = (0, 1, 2, 5, 10, 99, 999)


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
    try:
        accepted = SpecifierSet(str(requirement))
        candidates = _candidates(interpreter)
    except (InvalidSpecifier, InvalidVersion) as error:
        message = (
            f"requires-python {requirement!r} or interpreter {interpreter!r} "
            f"is invalid: {error}"
        )
        return [message]
    if not isinstance(requirement, str):
        return [f"requires-python must be a string, not {requirement!r}"]
    if any(accepted.contains(version, prereleases=True) for version in candidates):
        return []
    message = (
        f"the configured interpreter {interpreter!r} is outside the project's "
        f"requires-python {requirement!r}, so `uv sync` would refuse it"
    )
    return [message]


def _candidates(interpreter: str) -> list[Version]:
    """Return the versions an interpreter request can resolve to."""
    version = Version(interpreter)
    if len(version.release) >= 3:
        return [version]
    major, minor = version.release[0], version.release[1]
    return [Version(f"{major}.{minor}.{patch}") for patch in PATCHES]
