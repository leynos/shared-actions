"""Establishing that a declared command would run, without running it.

A prerequisite named in a declaration is either resolvable or it is not, and
the finding has to say which without starting it. A path is checked as a file
the operating system may execute; a bare name is looked up on the ``PATH`` of
the environment the command will actually receive, so a check that declares a
``PATH`` selects the executable it names.
"""

from __future__ import annotations

import os
import shutil
import typing as typ
from pathlib import Path


def _is_path(name: str) -> bool:
    """Return whether *name* is a path rather than a bare command name."""
    separators = [separator for separator in (os.sep, os.altsep) if separator]
    return any(separator in name for separator in separators)


def _executable(name: str, cwd: Path) -> str:
    """Return the name to look up, anchored at *cwd* when it is a relative path.

    A bare name is resolved through ``PATH``. A name carrying a path separator
    would otherwise be resolved against the process's working directory, which
    is not the directory the command is about to run in.

    The anchor keeps the declaration's own parent components. Normalising them
    away would interpret ``..`` before the operating system follows a symlink
    in the path, so ``link/../probe`` would name a sibling of the link rather
    than a sibling of its target. The path is passed through as written, and
    the operating system resolves it.

    The anchor is also lexical in the sense that the final link is not
    followed: a virtual environment's ``bin/python`` is usually a symlink, and
    an interpreter finds its own ``site-packages`` from the path it was started
    with. Following the link here would run the target interpreter under the
    link's path and lose the environment the declaration named.
    """
    if not _is_path(name) or Path(name).is_absolute():
        return name
    return str(Path(cwd) / name)


def _unresolved(name: str, cwd: Path) -> str:
    """Return why *name* cannot be looked up, in the terms its declaration used."""
    if _is_path(name):
        return f"{name!r} is not an executable file in {str(cwd)!r}"
    return f"{name!r} is not on PATH"


def _search_path(environment: typ.Mapping[str, str]) -> str | None:
    """Return the ``PATH`` *environment* carries, under either spelling."""
    return environment.get("PATH") or environment.get("Path")


def resolve(
    name: str, cwd: Path, environment: typ.Mapping[str, str]
) -> tuple[str | None, str]:
    """Return the executable *name* resolves to, or why it does not.

    An explicit path is checked as a file the operating system may execute,
    following a symlink to its target, without running it. A bare name is
    looked up on the ``PATH`` the invocation's own environment carries, so a
    check that declares a ``PATH`` selects the executable it names rather than
    whichever one the helper's ambient ``PATH`` happens to hold.
    """
    if _is_path(name):
        candidate = Path(_executable(name, cwd))
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return None, _unresolved(name, cwd)
        return str(candidate), ""
    search = _search_path(environment)
    # An environment without a PATH gives the child nothing to search, so the
    # name cannot resolve even though the helper's own PATH could find it.
    found = None if search is None else shutil.which(name, path=search)
    if found is None:
        return None, _unresolved(name, cwd)
    return found, ""


def resolution_problem(name: str, cwd: Path, environment: typ.Mapping[str, str]) -> str:
    """Return why *name* cannot be resolved, or ``""`` when it can.

    Parameters
    ----------
    name : str
        The command name or path to look up.
    cwd : Path
        The directory a relative path is anchored at.
    environment : Mapping[str, str]
        The environment the command will run in. A bare name is looked up on
        its ``PATH``.

    Returns
    -------
    str
        The reason the name does not resolve, or an empty string when it does.

    """
    _, problem = resolve(name, cwd, environment)
    return problem
