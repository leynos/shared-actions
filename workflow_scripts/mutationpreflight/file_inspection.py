"""Inspecting one path, and reading one document, without hiding a refusal.

The filesystem answers through ``stat``, not through ``Path.exists`` or
``Path.is_file``: from Python 3.14 those predicates swallow every ``OSError``
and answer ``False``, so a document the process may not inspect would be
reported as an absent one. Each reader in this package asks these helpers and
keeps the refusal with the file it belongs to.
"""

from __future__ import annotations

import stat
import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


def inspect_path(path: Path) -> tuple[bool, bool, str | None]:
    """Return presence, regularity and why *path* could not be inspected.

    The three answers are kept apart so a reader can tell an absent file from a
    present path it must refuse: a directory or a dangling link is not absence,
    and saying it were would hide a declaration the author wrote.
    """
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return _target_missing(path)
    except OSError as error:
        return False, False, f"{path} could not be inspected: {error}"
    return True, stat.S_ISREG(info.st_mode), None


def read_toml(path: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return *path* parsed as TOML, or why it could not be read."""
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return None, f"{path} could not be read: {error}"
    return parsed, None


def _target_missing(path: Path) -> tuple[bool, bool, str | None]:
    """Return presence for a path whose target the filesystem cannot find.

    ``stat`` follows a symbolic link, so a dangling one reads as absent.
    ``lstat`` reports the link itself, which is present and unreadable rather
    than missing.
    """
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False, False, None
    except OSError as error:
        return False, False, f"{path} could not be inspected: {error}"
    return True, stat.S_ISREG(info.st_mode), None
