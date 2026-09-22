"""Contract that the developers' guide prescribes a runner snippet that folds.

The guide is where the next lane's `runs-on` is copied from, so a
snippet carrying the folding defect reproduces it in every workflow
written afterwards. No workflow contract can catch that, because the
defect is in the prescription rather than in a workflow yet.

`test_runner_placement.py` holds the rule for the workflows themselves.
This module holds the rule for the document that tells an author how to
write one.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]


#: The guide marks a snippet that is deliberately broken by preceding it
#: with this comment. Without the marker the guide could not show the
#: defect it warns about, and the contract below could not tell a
#: prescription from an illustration.
COUNTER_EXAMPLE_MARKER: typ.Final[str] = "<!-- folding-counter-example:"

DEVELOPERS_GUIDE: typ.Final[Path] = REPOSITORY_ROOT / "docs" / "developers-guide.md"


def _yaml_fences(lines: cabc.Sequence[str]) -> cabc.Iterator[tuple[int, str]]:
    """Yield each fenced YAML block's opening line number and its body.

    The line number is the fence's own, so a failure names the block a
    reader has to open rather than a line inside it.
    """
    opened: int | None = None
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if opened is None:
            if stripped == "```yaml":
                opened, body = number, []
        elif stripped == "```":
            yield opened, "\n".join(body)
            opened = None
        else:
            body.append(line)


def _marked_fence_lines(lines: cabc.Sequence[str]) -> frozenset[int]:
    """Return the opening line of each fence a counter-example marker introduces.

    The marker applies to the next fence and to nothing else, so any
    intervening prose clears it. A blank line does not, because the
    marker and its fence are separated by one.
    """
    marked: set[int] = set()
    pending = False
    for number, line in enumerate(lines, start=1):
        was_pending, pending = pending, _marker_state(line, pending=pending)
        if was_pending and line.strip() == "```yaml":
            marked.add(number)
    return frozenset(marked)


def _marker_state(line: str, *, pending: bool) -> bool:
    """Return whether a marker is still pending after reading *line*.

    A marker line sets it. Any other line carrying text clears it,
    because the marker applies to the next fence and to nothing further.
    A blank line leaves it alone, since the marker and its fence are
    separated by one.
    """
    if line.startswith(COUNTER_EXAMPLE_MARKER):
        return True
    return pending if not line.strip() else False


def _guide_runner_snippets() -> list[tuple[int, bool, str]]:
    """Return each guide YAML block declaring a runner, with its marker state."""
    lines = DEVELOPERS_GUIDE.read_text(encoding="utf-8").splitlines()
    marked = _marked_fence_lines(lines)
    return [
        (number, number in marked, body)
        for number, body in _yaml_fences(lines)
        if "runs-on:" in body
    ]


def _guide_snippet_identifier(case: tuple[int, bool, str]) -> str:
    """Name a guide snippet by its fence line and marker state."""
    line, marked, _ = case
    return f"line-{line}-{'counter-example' if marked else 'prescribed'}"


@pytest.mark.parametrize(
    "snippet", _guide_runner_snippets(), ids=_guide_snippet_identifier
)
def test_the_guide_prescribes_a_runner_snippet_that_folds(
    snippet: tuple[int, bool, str],
) -> None:
    """A snippet the guide prescribes parses to one line; a marked one does not.

    The guide is where the next lane's `runs-on` is copied from, so a
    prescribed snippet carrying the folding defect reproduces it in every
    workflow written afterwards, and no workflow contract can catch that
    because the defect is not yet in a workflow. Both directions are
    asserted: a counter-example that quietly became correct would stop
    showing the failure the surrounding prose explains, and the marker
    would then be a way to exempt a real prescription from the rule.
    """
    line, marked, body = snippet
    value = yaml.safe_load(body)["runs-on"]
    carries_a_break = "\n" in value
    if marked:
        assert carries_a_break, (
            f"the snippet at docs/developers-guide.md:{line} is marked as a "
            "folding counter-example but parses to one line; either restore "
            "the deeper continuation indent or drop the marker"
        )
        return
    assert not carries_a_break, (
        f"the snippet at docs/developers-guide.md:{line} prescribes a "
        f"runner declaration that parses to {value!r}, with a line break "
        "inside the expression; keep the continuation at the same indent as "
        "its first line"
    )
