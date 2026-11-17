"""Reading the TOML documents a declaration and a lock are kept in.

Both files are untrusted input from the caller's repository, so every failure
comes back as a finding naming the file rather than as an exception.
"""

from __future__ import annotations

import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


def read_document(path: Path) -> tuple[dict[str, object] | None, str | None]:
    """Return *path* parsed as TOML, or why it could not be read.

    Parameters
    ----------
    path : Path
        The file to parse.

    Returns
    -------
    tuple[dict[str, object] | None, str | None]
        The parsed document, and the finding that replaced it when it could
        not be read.

    """
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        message = f"{path} could not be read: {error}"
        return None, message
    return parsed, None


def nested_table(
    document: dict[str, object], *keys: str
) -> dict[str, object] | None:
    """Return the table at *keys*, or ``None`` when the path is not a table.

    Parameters
    ----------
    document : dict[str, object]
        The parsed document to walk.
    *keys : str
        The table path, outermost first. No keys returns *document* itself.

    Returns
    -------
    dict[str, object] | None
        The table, or ``None`` when a key is absent or is not a table.

    """
    current: object = document
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None
