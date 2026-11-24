"""Load the fenced examples of a published document, marked for testing.

Every fence must follow a ``<!-- tested-example: <identifier> -->`` marker, so
a test exercises the text readers copy rather than a fixture beside it. An
unmarked fence, an unterminated fence or a duplicate identifier is an error.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

_MARKER: typ.Final[re.Pattern[str]] = re.compile(
    r"^<!-- tested-example: (?P<identifier>[A-Za-z0-9._-]+) -->$"
)
_FENCE: typ.Final[str] = "```"


def load_tested_examples(path: Path) -> dict[str, str]:
    """Return each marked example in *path*, keyed by its identifier.

    Raises
    ------
    ValueError
        When a fence has no marker immediately before it, a fence is never
        closed, or an identifier is used twice.
    """
    examples: dict[str, str] = {}
    pending: str | None = None
    body: list[str] | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if body is not None:
            if line.startswith(_FENCE):
                examples[typ.cast("str", pending)] = "\n".join(body) + "\n"
                pending, body = None, None
            else:
                body.append(line)
        elif line.startswith(_FENCE):
            if pending is None:
                message = f"{path}:{number}: fence without a tested-example marker"
                raise ValueError(message)
            body = []
        elif match := _MARKER.match(line):
            identifier = match["identifier"]
            if identifier in examples:
                message = f"{path}:{number}: duplicate example {identifier!r}"
                raise ValueError(message)
            pending = identifier
        elif line.strip():
            pending = None
    if body is not None:
        message = f"{path}: unterminated fence"
        raise ValueError(message)
    return examples
