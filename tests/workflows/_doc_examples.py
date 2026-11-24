"""Load the fenced examples of a published document, marked for testing.

Every fence must follow a ``<!-- tested-example: <identifier> -->`` marker, so
a test exercises the text readers copy rather than a fixture beside it. An
unmarked fence, an unterminated fence or a duplicate identifier is an error.
Backtick and tilde fences are both recognized, and a fence closes only on a
fence of the same character at least as long as the one that opened it.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_MARKER: typ.Final[re.Pattern[str]] = re.compile(
    r"^<!-- tested-example: (?P<identifier>[A-Za-z0-9._-]+) -->$"
)
_FENCE: typ.Final[re.Pattern[str]] = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
#: A fence inside a block quote or a list item is still a fence a reader can
#: copy, but it cannot be marked, so it is refused rather than skipped.
_CONTAINED_FENCE: typ.Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)+(?:`{3,}|~{3,})"
)


@dataclasses.dataclass(slots=True)
class _Open:
    """A fence being read: its marker, delimiter and the lines so far."""

    identifier: str
    delimiter: str
    lines: list[str] = dataclasses.field(default_factory=list)

    def closes_on(self, line: str) -> bool:
        """Return whether *line* closes this fence."""
        found = _FENCE.match(line)
        if found is None or line.strip() != found["fence"]:
            return False
        fence = found["fence"]
        return fence[0] == self.delimiter[0] and len(fence) >= len(self.delimiter)


def _opening(line: str, marker: str | None, where: str) -> _Open:
    """Return the fence *line* opens, which *marker* must name."""
    if marker is None:
        message = f"{where}: fence without a tested-example marker"
        raise ValueError(message)
    found = _FENCE.match(line)
    return _Open(marker, typ.cast("re.Match[str]", found)["fence"])


def _marker_for(line: str, known: cabc.Container[str], where: str) -> str | None:
    """Return the identifier *line* declares, or ``None`` if it declares none."""
    found = _MARKER.match(line)
    if found is None:
        return None
    if found["identifier"] in known:
        message = f"{where}: duplicate example {found['identifier']!r}"
        raise ValueError(message)
    return found["identifier"]


def _next_marker(
    line: str, marker: str | None, known: cabc.Container[str], where: str
) -> str | None:
    """Return the marker in force after *line*: a new one, kept over a blank."""
    declared = _marker_for(line, known, where)
    if declared is not None:
        return declared
    return marker if not line.strip() else None


def load_tested_examples(path: Path) -> dict[str, str]:
    """Return each marked example in *path*, keyed by its identifier.

    Raises
    ------
    ValueError
        When a fence has no marker immediately before it, a fence is never
        closed, or an identifier is used twice.
    """
    examples: dict[str, str] = {}
    marker: str | None = None
    current: _Open | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{path}:{number}"
        if current is not None:
            if current.closes_on(line):
                examples[current.identifier] = "\n".join(current.lines) + "\n"
                current = None
            else:
                current.lines.append(line)
        elif _CONTAINED_FENCE.match(line):
            message = f"{where}: fence inside a block quote or list item"
            raise ValueError(message)
        elif _FENCE.match(line):
            current, marker = _opening(line, marker, where), None
        else:
            marker = _next_marker(line, marker, examples, where)
    if current is not None:
        message = f"{path}: unterminated fence"
        raise ValueError(message)
    return examples
