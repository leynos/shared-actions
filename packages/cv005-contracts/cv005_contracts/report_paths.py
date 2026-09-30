"""Decide whether an artefact `path` entry could carry the coverage report.

A lane keeps its report out of `upload-artifact` with `publish-artefact:
'false'`, so a step that uploads the report some other way defeats it. Naming
the report is the least of the ways to do that: `.`, `..`, a directory the
report sits in, a glob that matches it, an absolute path above the workspace
and an expression the reader cannot resolve all publish it without spelling
its name. The question is whether the report *can* leave the runner, so each
entry is judged by what it could select, and an entry the reader cannot
resolve is refused rather than cleared.
"""

from __future__ import annotations

import itertools
import re
import typing as typ

from .expressions import ConditionError, yielded_operands

#: The roots an absolute path may sit under and still be cleared. An absolute
#: path in general can be `/`, `/home/runner/work` or a glob over `/home`,
#: each of which holds the workspace and the report in it.
SCRATCH_ROOTS: typ.Final[tuple[str, ...]] = ("/tmp/",)  # noqa: S108 - a path prefix, not a file created here

_EXPRESSION: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{(?P<body>.*)\}\}$", re.DOTALL
)
_LITERAL: typ.Final[re.Pattern[str]] = re.compile(r"^'((?:[^']|'')*)'$")
_GLOB_CHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")
_BRACES: typ.Final[re.Pattern[str]] = re.compile(r"\{([^{}]*)\}")


def entries_of(path: object) -> list[str]:
    r"""Return the entries of an `upload-artifact` `path`, one per line.

    Parameters
    ----------
    path : object
        The step's `path` input, normally a string.

    Returns
    -------
    list[str]
        The stripped, non-blank lines; empty when there is no path.

    Examples
    --------
    >>> entries_of("dist/\n  logs/*.txt\n\n")
    ['dist/', 'logs/*.txt']
    >>> entries_of(None)
    []

    """
    if path is None:
        return []
    return [line.strip() for line in str(path).splitlines() if line.strip()]


def could_hold_the_report(entry: str, report: str) -> bool:
    """Return whether one `path` entry could carry the coverage report.

    Parameters
    ----------
    entry : str
        One stripped, non-blank line of an artefact `path`.
    report : str
        The lane's `output-path`, relative to the workspace.

    Returns
    -------
    bool
        True when the entry names the report, the workspace or a directory
        holding it, matches it as a glob, or cannot be shown to stay outside
        the workspace.

    Examples
    --------
    >>> could_hold_the_report(".", "lcov.info")
    True
    >>> could_hold_the_report("target/", "lcov.info")
    False
    >>> could_hold_the_report("*.info", "lcov.info")
    True
    >>> could_hold_the_report("${{ x && '/tmp/a.log' || '' }}", "lcov.info")
    False

    """
    if entry.startswith("!"):
        # A negation only removes files from what the other entries select.
        return False
    if report in entry:
        return True
    if entry.startswith(("/", "~")):
        return not _in_scratch(entry)
    if "${{" in entry:
        return not _expression_stays_outside(entry)
    if ".." in entry.split("/"):
        return True
    if _GLOB_CHARACTERS & set(entry) or "{" in entry:
        return _glob_selects(entry, report)
    return _names_the_workspace_or_a_parent_of(entry, report)


def _parts(path: str) -> list[str]:
    """Return a relative path's components, without `.` and empty ones."""
    return [part for part in path.split("/") if part not in ("", ".")]


def _names_the_workspace_or_a_parent_of(entry: str, report: str) -> bool:
    """Return whether a plain relative entry is the workspace or holds the report."""
    parts = _parts(entry)
    return _parts(report)[: len(parts)] == parts


def _in_scratch(entry: str) -> bool:
    """Return whether a path sits under a scratch root and never climbs out."""
    return entry.startswith(SCRATCH_ROOTS) and ".." not in entry.split("/")


def _expression_stays_outside(entry: str) -> bool:
    """Return whether every result a whole expression can yield is scratch.

    Cleared only when every alternative ends in a quoted literal that is a
    scratch path or empty.
    """
    match = _EXPRESSION.match(entry)
    if match is None:
        return False
    try:
        operands = yielded_operands(entry)
    except ConditionError:
        return False
    literals = [_LITERAL.match(operand) for operand in operands]
    if not all(literals):
        return False
    values = [
        literal.group(1).replace("''", "'")
        for literal in literals
        if literal is not None
    ]
    return all(not value or _in_scratch(value) for value in values)


def _expand_braces(pattern: str) -> list[str]:
    """Return a pattern with each `{a,b}` group expanded into its alternatives."""
    match = _BRACES.search(pattern)
    if match is None:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return list(
        itertools.chain.from_iterable(
            _expand_braces(head + option + tail) for option in match.group(1).split(",")
        )
    )


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regular expression over `/`-separated paths."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        piece, index = _glob_piece(pattern, index)
        out.append(piece)
    return re.compile("".join(out) + r"\Z")


def _glob_piece(pattern: str, index: int) -> tuple[str, int]:
    """Translate the glob element at `index`; return it and the next index."""
    if pattern.startswith("**/", index):
        return r"(?:[^/]+/)*", index + 3
    if pattern.startswith("**", index):
        return ".*", index + 2
    char = pattern[index]
    if char == "*":
        return "[^/]*", index + 1
    if char == "?":
        return "[^/]", index + 1
    end = pattern.find("]", index + 1)
    if char == "[" and end > 0:
        body = pattern[index + 1 : end]
        negated = body.startswith("!")
        return f"[{'^' if negated else ''}{re.escape(body[negated:])}]", end + 1
    return re.escape(char), index + 1


def _glob_selects(entry: str, report: str) -> bool:
    """Return whether a glob matches the report or a directory holding it.

    A pattern that matches a directory uploads everything beneath it, so each
    ancestor of the report is tried as well as the report itself.
    """
    parts = _parts(report)
    candidates = ["/".join(parts[: count + 1]) for count in range(len(parts))]
    return any(
        _glob_regex("/".join(_parts(pattern))).match(candidate)
        for pattern in _expand_braces(entry)
        for candidate in candidates
    )
