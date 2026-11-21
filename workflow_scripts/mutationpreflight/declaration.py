"""Reading the requirements a repository declares for its mutation run.

One declaration per repository, in one of two places. Declaring in both is
refused rather than ranked, because which file binds the run would otherwise
be a fact about this helper's precedence rather than the repository's intent.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from .constants import (
    CHECK_KEYS,
    COMMAND_KEYS,
    DECLARATION_KEYS,
    MAX_CHECK_TIMEOUT_SECONDS,
    NATIVE_KEYS,
)
from .models import (
    Check,
    CommandRequirement,
    Declaration,
    DeclarationError,
    Loaded,
    NativeRequirement,
)
from .schema import (
    entry,
    positive_int,
    refuse,
    refuse_unknown,
    required_string,
    required_strings,
    string,
    strings,
)

if typ.TYPE_CHECKING:
    from .ports import Document, RepositoryFiles

#: Where a declaration may live, in the order a repository's intent is read.
_DECLARATION_LOCATIONS: typ.Final[tuple[Path, ...]] = (
    Path("pyproject.toml"),
    Path(".github") / "mutation-preflight.toml",
)

#: The table path inside each file a declaration may live in. The sidecar is a
#: bare table, so its path is empty.
_LOCATION_KEYS: typ.Final[dict[str, tuple[str, ...]]] = {
    "pyproject.toml": ("tool", "mutation-preflight"),
    "mutation-preflight.toml": (),
}

_BOTH_DECLARED: typ.Final[str] = (
    "both pyproject.toml's [tool.mutation-preflight] and "
    ".github/mutation-preflight.toml declare requirements; keep one, "
    "because which one binds the run would otherwise depend on this "
    "helper's precedence"
)

_SANDBOX_ON_CARGO: typ.Final[str] = (
    "sandbox_paths is a mutmut-lane requirement; cargo-mutants copies the "
    "whole working tree, so declare the tools it needs instead"
)

_DEFAULT_TIMEOUT_SECONDS: typ.Final[int] = 300


def _declaration_member(
    document: dict[str, object], keys: tuple[str, ...]
) -> tuple[bool, dict[str, object] | None]:
    """Return whether *keys* names a member, and that member when it is a table."""
    current: object = document
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = typ.cast("dict[str, object]", current)[key]
    return True, current if isinstance(current, dict) else None


def _declared_tables(
    documents: typ.Sequence[Document],
) -> tuple[list[tuple[Path, dict[str, object]]], Path | None, list[str]]:
    """Return every declaration table found, or the file that would not parse."""
    found: list[tuple[Path, dict[str, object]]] = []
    for document in documents:
        if document.problem:
            return found, document.path, [document.problem]
        keys = _LOCATION_KEYS[document.path.name]
        present, table = _declaration_member(document.document or {}, keys)
        if present and table is None:
            # Absent and misdeclared are different: one means the lane has not
            # opted in, the other is a declaration this helper cannot honour.
            message = (
                f"{document.path} declares {'.'.join(keys)} but not as a table, "
                f"so its requirements can be neither read nor ignored"
            )
            return found, document.path, [message]
        if table is not None:
            found.append((document.path, table))
    return found, None, []


def _declaration_table(
    documents: typ.Sequence[Document],
) -> tuple[Path | None, dict[str, object] | None, list[str]]:
    """Return the declaration's file and table, insisting on one declaration."""
    found, failed, problems = _declared_tables(documents)
    if failed is not None:
        return failed, None, problems
    if len(found) > 1:
        return found[0][0], None, [_BOTH_DECLARED]
    if not found:
        return None, None, []
    path, table = found[0]
    return path, table, []


def _command_requirement(raw: object, index: int) -> CommandRequirement:
    """Return one ``require_commands`` entry, refusing an unexpected shape."""
    where = f"require_commands[{index}]"
    table = entry(raw, COMMAND_KEYS, where)
    return CommandRequirement(
        name=required_string(table, "name", where),
        version=string(table, "version", where),
        version_args=strings(table.get("version_args"), f"{where}.version_args")
        or ("--version",),
    )


def _native_requirement(raw: object) -> NativeRequirement:
    """Return the ``native`` table, refusing an unexpected shape."""
    table = entry(raw, NATIVE_KEYS, "native")
    return NativeRequirement(
        package=required_string(table, "package", "native"),
        revision=string(table, "revision", "native"),
        supported_python=string(table, "supported_python", "native"),
        probe=strings(table.get("probe"), "native.probe"),
    )


def _check_timeout(table: dict[str, object], where: str) -> int:
    """Return the bounded wall-clock ceiling one check may ask for."""
    value = table.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    if not positive_int(value):
        refuse(f"{where}.timeout_seconds", "a positive integer")
    return min(typ.cast("int", value), MAX_CHECK_TIMEOUT_SECONDS)


def _unpassable(key: str, item: str) -> bool:
    """Return whether the operating system cannot pass the entry *key*=*item*."""
    if not key:
        return True
    if "=" in key:
        return True
    return "\x00" in key or "\x00" in item


def _environment(table: dict[str, object], where: str) -> dict[str, object]:
    """Return one check's environment table, refusing any other shape."""
    value = table.get("env", {})
    if not isinstance(value, dict):
        refuse(f"{where}.env", "a table of strings")
    return typ.cast("dict[str, object]", value)


def _check_environment(
    table: dict[str, object], where: str
) -> tuple[tuple[str, str], ...]:
    """Return one check's environment, refusing anything but a string table.

    A key carrying ``=`` or a NUL cannot be passed to the operating system, so
    it is refused here rather than surfacing as a spawn error the phase cannot
    attribute.
    """
    pairs = _environment(table, where)
    for key, item in pairs.items():
        if not isinstance(key, str) or not isinstance(item, str):
            refuse(f"{where}.env", "a table of strings")
        if _unpassable(key, item):
            refuse(
                f"{where}.env",
                "a table whose keys and values the operating system can pass",
            )
    return tuple(sorted(typ.cast("dict[str, str]", pairs).items()))


def _check(raw: object, index: int) -> Check:
    """Return one ``checks`` entry, refusing an unexpected shape."""
    where = f"checks[{index}]"
    table = entry(raw, CHECK_KEYS, where)
    return Check(
        name=required_string(table, "name", where),
        command=required_strings(table, "command", where),
        cwd=string(table, "cwd", where) or ".",
        timeout_seconds=_check_timeout(table, where),
        env=_check_environment(table, where),
    )


def _commands(table: dict[str, object]) -> tuple[CommandRequirement, ...]:
    """Return every declared command requirement."""
    raw = table.get("require_commands", [])
    if not isinstance(raw, list):
        refuse("require_commands", "an array of tables")
    entries = typ.cast("list[object]", raw)
    return tuple(
        _command_requirement(item, index) for index, item in enumerate(entries)
    )


def _checks(table: dict[str, object]) -> tuple[Check, ...]:
    """Return every declared check."""
    raw = table.get("checks", [])
    if not isinstance(raw, list):
        refuse("checks", "an array of tables")
    entries = typ.cast("list[object]", raw)
    return tuple(_check(item, index) for index, item in enumerate(entries))


def _native(table: dict[str, object]) -> NativeRequirement | None:
    """Return the declared native requirement, when there is one."""
    raw = table.get("native")
    return None if raw is None else _native_requirement(raw)


def _declaration(table: dict[str, object], source: Path, lane: str) -> Declaration:
    """Return the parsed declaration, refusing anything it cannot honour."""
    refuse_unknown(table, DECLARATION_KEYS, str(source))
    declaration = Declaration(
        source=source,
        lane=lane,
        sandbox_paths=strings(table.get("sandbox_paths"), "sandbox_paths"),
        commands=_commands(table),
        native=_native(table),
        checks=_checks(table),
    )
    if lane != "mutmut" and declaration.sandbox_paths:
        raise DeclarationError(_SANDBOX_ON_CARGO)
    return declaration


def load_declaration(files: RepositoryFiles, root: Path, lane: str) -> Loaded:
    """Return the repository's declaration for *lane*, or why it was refused.

    Parameters
    ----------
    files : RepositoryFiles
        How the declaration's documents are read.
    root : Path
        The checkout the declaration belongs to.
    lane : str
        ``mutmut`` or ``cargo``.

    Returns
    -------
    Loaded
        The source read, the parsed declaration when it was accepted, and the
        findings that refused it otherwise. A repository declaring nothing
        yields a ``None`` source and no findings: silence is not a failure, it
        is a lane that has not opted in.

    """
    source, table, findings = _declaration_table(
        files.read_documents(root, _DECLARATION_LOCATIONS)
    )
    if source is None or table is None:
        return Loaded(source, None, tuple(findings))
    try:
        declaration = _declaration(table, source, lane)
    except DeclarationError as error:
        return Loaded(source, None, (*findings, str(error)))
    return Loaded(source, declaration, tuple(findings))
