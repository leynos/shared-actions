"""Loading the executable examples a published document carries.

A fenced block is an example when the line immediately above its opening fence
is ``<!-- tested-example: <identifier> -->``. Loading fails on an unmarked
fence, an unterminated fence, a marker that precedes no fence, a marker
separated from its fence by prose or a blank line, a fence nested inside a
blockquote, and a duplicate identifier, so a document
cannot drift away from the examples its tests load from the shipped text rather
than from a copied fixture.

Validation raises rather than asserts: only ``test_*.py`` files are exempt from
``S101``, and this is a support module. The message is the same either way, and
the failure still propagates as the ``AssertionError`` a suite reports.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

_MARKER = re.compile(r"^<!--\s*tested-example:\s*(?P<identifier>\S+)\s*-->$")
_FENCE = re.compile(r"^(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_BLOCKQUOTE = re.compile(r"^\s*(?:>\s?)+")


class ExampleError(AssertionError):
    """Raised when a document's fenced examples cannot be loaded."""


@dc.dataclass(frozen=True, slots=True)
class Example:
    """One marked fenced block, as the document publishes it.

    Attributes
    ----------
    identifier : str
        The stable name its marker gives it.
    language : str
        The info string after the opening fence, such as ``toml``.
    body : str
        The fence's contents, without the fence lines.

    """

    identifier: str
    language: str
    body: str


@dc.dataclass(frozen=True, slots=True)
class _Located:
    """One fence's contents, and the line the document opens it on."""

    line_number: int
    language: str
    body: str


def _fence_closes(line: str, fence: str) -> bool:
    """Return whether *line* closes a fence opened by *fence*."""
    stripped = line.strip()
    return set(stripped) == {fence[0]} and stripped.startswith(fence)


def _marker(line: str) -> str | None:
    """Return the identifier *line* marks, or ``None`` when it marks nothing."""
    matched = _MARKER.match(line.strip())
    return None if matched is None else matched.group("identifier")


def _opening(line: str) -> tuple[str, str] | None:
    """Return a fence's delimiter and language, or ``None`` when it opens none."""
    matched = _FENCE.match(line.strip())
    if matched is None:
        return None
    return matched.group("fence"), matched.group("info").strip()


def _container_fence(line: str) -> bool:
    """Return whether *line* fences an example inside a blockquote.

    GitHub-flavoured Markdown permits that nesting, so the line would otherwise
    pass for prose and hide an example this loader never reads.
    """
    unquoted = _BLOCKQUOTE.sub("", line)
    return unquoted != line and _opening(unquoted) is not None


def _refuse_container(path: Path, line: str, line_number: int) -> None:
    """Refuse a blockquote fence rather than silently ignoring it."""
    if not _container_fence(line):
        return
    message = (
        f"{path}:{line_number}: a fenced block inside a blockquote is not "
        f"supported; publish the example at the document's top level"
    )
    raise ExampleError(message)


def _read_body(path: Path, lines: list[str], start: int, fence: str) -> tuple[str, int]:
    """Return one fence's body and the index of the line after its close.

    *start* indexes the body's first line, so it is also the 1-based number of
    the line that opened the fence.
    """
    body: list[str] = []
    for index in range(start, len(lines)):
        if _fence_closes(lines[index], fence):
            return "\n".join(body), index + 1
        body.append(lines[index])
    message = f"{path}:{start}: the fence opened here is never closed"
    raise ExampleError(message)


def _record(
    path: Path, examples: dict[str, Example], pending: str | None, located: _Located
) -> None:
    """Record one fence under the marker above it, refusing a missing or reused name."""
    if pending is None:
        message = (
            f"{path}:{located.line_number}: the fenced block has no "
            f"tested-example marker"
        )
        raise ExampleError(message)
    if pending in examples:
        message = (
            f"{path}:{located.line_number}: duplicate tested-example "
            f"identifier {pending!r}"
        )
        raise ExampleError(message)
    examples[pending] = Example(pending, located.language, located.body)


def _pending_marker(
    path: Path, pending: str | None, identifier: str, line_number: int
) -> str:
    """Return the marker now pending, refusing one that never reached a fence."""
    if pending is not None:
        message = f"{path}:{line_number}: the marker for {pending!r} precedes no fence"
        raise ExampleError(message)
    return identifier


def _intervening(
    pending: str | None, prose: int | None, line_number: int
) -> int | None:
    """Return the line prose first intervenes on, if it does.

    A blank line counts: the marker must be the line above its fence, so even
    an empty line between them is a separation, not layout.
    """
    if pending is None:
        return prose
    if prose is not None:
        return prose
    return line_number


def _refuse_prose(path: Path, pending: str | None, prose: int | None) -> None:
    """Refuse a fence that its marker's prose separated from it."""
    if prose is None:
        return
    message = (
        f"{path}:{prose}: the marker for {pending!r} must be the "
        f"line above its fence; prose intervenes"
    )
    raise ExampleError(message)


def _refuse_unfenced(path: Path, pending: str | None) -> None:
    """Refuse a document whose last marker reached no fence."""
    if pending is not None:
        message = f"{path}: the marker for {pending!r} precedes no fence"
        raise ExampleError(message)


def load_examples(path: Path) -> dict[str, Example]:
    """Return every marked example in *path*, refusals named by line.

    Parameters
    ----------
    path : Path
        The Markdown document to read.

    Returns
    -------
    dict[str, Example]
        The examples, keyed by their marker identifiers, in document order.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    examples: dict[str, Example] = {}
    pending: str | None = None
    prose: int | None = None
    index = 0
    while index < len(lines):
        identifier = _marker(lines[index])
        if identifier is not None:
            pending = _pending_marker(path, pending, identifier, index + 1)
            prose = None
            index += 1
            continue
        opening = _opening(lines[index])
        if opening is None:
            _refuse_container(path, lines[index], index + 1)
            # Prose between a marker and its fence is only a separation if a
            # fence follows; without one the marker precedes no fence at all,
            # which the end of the document reports.
            prose = _intervening(pending, prose, index + 1)
            index += 1
            continue
        _refuse_prose(path, pending, prose)
        fence, language = opening
        body, after = _read_body(path, lines, index + 1, fence)
        _record(path, examples, pending, _Located(index + 1, language, body))
        pending = None
        prose = None
        index = after
    _refuse_unfenced(path, pending)
    return examples
