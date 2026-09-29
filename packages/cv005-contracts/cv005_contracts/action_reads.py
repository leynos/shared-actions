"""Find the environment keys a `generate-coverage` action reads.

The parity rule leaves out environment keys that only pin or place a tool
(`*_VERSION`, `*_REV`, `*_SHA256*`, uv's directories, Cargo's retries), on the
ground that they do not change what is measured. That holds only while the
action never reads one of them: a `CARGO_LLVM_COV_VERSION` the action passes
to its installer changes the measuring tool, so two legs differing in it
measure differently and must not be treated as equal.

This module reads the action's own files, so a contract can hold the hand
written exclusion to what the action does. Python scripts are read for the
string constants that look like environment names, which is where `os.environ`
lookups keep them; the action's YAML is read for `env:` keys, `env.X`
references and shell `$X` expansions.
"""

from __future__ import annotations

import ast
import re
import typing as typ

import yaml

from .parity import is_measured, matches_exclusion

if typ.TYPE_CHECKING:
    from pathlib import Path

#: An environment variable's conventional spelling.
_ENV_NAME: typ.Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z0-9_]*")
_SHELL_READ: typ.Final[re.Pattern[str]] = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)")
_CONTEXT_READ: typ.Final[re.Pattern[str]] = re.compile(r"\benv\.([A-Za-z0-9_]+)")
#: Directories under the action holding tests and caches, not action code.
_SKIPPED_DIRECTORIES: typ.Final[frozenset[str]] = frozenset({"tests", "__pycache__"})


def keys_read(action_dir: Path) -> set[str]:
    """Return the environment keys an action's files read from the caller.

    Keys the action's own steps assign under `env:` are left out: the step's
    value replaces whatever the caller set, so the caller cannot vary them.

    Parameters
    ----------
    action_dir : Path
        The action's directory, holding `action.yml` and its `scripts`.

    Returns
    -------
    set[str]
        The names read, from the action's YAML and its scripts.

    Raises
    ------
    FileNotFoundError
        If the directory holds no `action.yml`, since an empty reading would
        find nothing and so pass.

    """
    text = (action_dir / "action.yml").read_text(encoding="utf-8")
    assigned, read = _yaml_env(yaml.safe_load(text), text)
    for path in sorted((action_dir / "scripts").rglob("*")):
        if path.is_file() and not _SKIPPED_DIRECTORIES & set(
            path.relative_to(action_dir).parts
        ):
            read |= _script_reads(path)
    return read - assigned


def _yaml_env(document: object, text: str) -> tuple[set[str], set[str]]:
    """Return the keys the YAML assigns under `env:` and the keys it reads."""
    assigned: set[str] = set()
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            env = node.get("env")
            assigned |= {str(key) for key in env} if isinstance(env, dict) else set()
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    read = set(_SHELL_READ.findall(text)) | set(_CONTEXT_READ.findall(text))
    return assigned, read


def _script_reads(path: Path) -> set[str]:
    """Return the names a script reads: Python constants, or shell expansions."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return set(_SHELL_READ.findall(text))
    return {
        node.value
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _ENV_NAME.fullmatch(node.value)
    }


def excluded_reads(action_dir: Path) -> list[str]:
    """Return the keys the action reads that the parity rule would not compare.

    Parameters
    ----------
    action_dir : Path
        The action's directory.

    Returns
    -------
    list[str]
        Each key read that `parity.is_measured` excludes, sorted. Empty when
        the exclusion and the action agree.

    """
    return sorted(key for key in keys_read(action_dir) if not is_measured(key))


def pattern_reads(action_dir: Path) -> set[str]:
    """Return the keys the action reads that a pattern of the exclusion matches.

    Parameters
    ----------
    action_dir : Path
        The action's directory.

    Returns
    -------
    set[str]
        Each key read that matches the exclusion's patterns, whether or not
        the carve-out keeps it compared. `parity.ACTION_READ_KEYS` must equal
        this, or a key is excluded that the action reads, or compared that it
        does not.

    """
    return {key for key in keys_read(action_dir) if matches_exclusion(key)}
