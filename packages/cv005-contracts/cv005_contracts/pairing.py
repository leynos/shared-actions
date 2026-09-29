"""Check the lane-to-publisher pairings a repository declares.

The estate rule pairs each lane generator with a publisher generator of the
same selection, by inspection. A matrix job, or a Windows baseline written by
a job of its own, defeats inspection: the lane's inputs are matrix
expressions and its legs are chosen by conditions. The repository then
declares each pair in `.github/cv005.toml`, and this module holds the
declaration to what the workflows say: both legs exist, the matrix cell
exists, the two legs differ on exactly the declared keys once the cell's
values are read in, and the conditions that choose the leg are exactly those
declared. A lane leg left unmapped falls back to the estate rule, which
refuses it.
"""

from __future__ import annotations

import itertools
import re
import typing as typ

from .expressions import ConditionError, conjuncts
from .legs import Leg, generator_legs
from .parity import selection

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .declared import Pairing
    from .loading import Document

#: The `if:` term every pull-request lane leg carries.
PULL_REQUEST_TERM: typ.Final[str] = "github.event_name == 'pull_request'"
_MATRIX_VALUE: typ.Final[re.Pattern[str]] = re.compile(
    r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}"
)
_ANY_MATRIX_READ: typ.Final[re.Pattern[str]] = re.compile(r"\$\{\{[^}]*\bmatrix\.")
#: A value no workflow can hold, standing for an absent key.
_ABSENT: typ.Final[object] = object()
#: The matrix keys that shape the matrix rather than name an axis.
_MATRIX_CONTROLS: typ.Final[frozenset[str]] = frozenset({"include", "exclude"})


def pairing_violations(
    publisher_name: str,
    publisher: Document,
    closure: dict[str, Document],
    pairings: cabc.Sequence[Pairing],
) -> list[str]:
    """Return every way the declared pairings disagree with the workflows.

    Parameters
    ----------
    publisher_name : str
        The publisher's file name, which begins each publisher leg's name.
    publisher : Document
        The publisher workflow document.
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.
    pairings : Sequence[Pairing]
        The declared pairings.

    Returns
    -------
    list[str]
        One message per disagreement; empty when every pairing holds.

    """
    lane_legs = {
        leg.ident: leg
        for name, document in sorted(closure.items())
        for leg in generator_legs(name, document)
    }
    publisher_legs = {
        leg.ident: leg for leg in generator_legs(publisher_name, publisher)
    }
    found: list[str] = []
    for pairing in pairings:
        lane = lane_legs.get(pairing.lane)
        other = publisher_legs.get(pairing.publisher)
        found += _missing_legs(pairing, lane, other)
        if lane is not None and other is not None:
            found += _pair_violations(pairing, lane, other)
    return found


def _missing_legs(pairing: Pairing, lane: Leg | None, other: Leg | None) -> list[str]:
    """Name each leg of a pairing that no workflow holds."""
    return [
        f"the pairing's {role} leg {name} does not exist"
        for role, name, leg in (
            ("lane", pairing.lane, lane),
            ("publisher", pairing.publisher, other),
        )
        if leg is None
    ]


def _pair_violations(pairing: Pairing, lane: Leg, publisher: Leg) -> list[str]:
    """Judge one pairing whose two legs both exist."""
    resolved, problems = _resolved_selection(pairing, lane)
    differing = _differing_keys(
        resolved, _flat(selection(publisher.document, publisher.step))
    )
    declared = set(pairing.differs)
    problems += [
        f"differs from {pairing.publisher} on {key!r}; declare it in `differs`"
        for key in sorted(differing - declared)
    ]
    problems += [
        f"declares a difference on {key!r} but the two legs agree"
        for key in sorted(declared - differing)
    ]
    problems += _cell_violations(pairing, lane) + _guard_violations(pairing, lane)
    return [f"{pairing.lane}: {problem}" for problem in problems]


def _resolved_selection(
    pairing: Pairing, lane: Leg
) -> tuple[dict[str, object], list[str]]:
    """Return the lane leg's selection with its matrix cell's values read in."""
    read: set[str] = set()
    problems: list[str] = []

    def replace(match: re.Match[str]) -> str:
        """Read one `${{ matrix.key }}` from the declared cell."""
        read.add(match.group(1))
        return pairing.matrix.get(match.group(1), match.group(0))

    def resolve(value: object) -> object:
        """Read the declared matrix values into one string value."""
        if not isinstance(value, str):
            return value
        text = _MATRIX_VALUE.sub(replace, value)
        if _ANY_MATRIX_READ.search(text):
            problems.append(f"reads a matrix value the pairing does not give: {text!r}")
        return text

    flat = _flat(selection(lane.document, lane.step))
    resolved = {key: resolve(value) for key, value in flat.items()}
    problems += [
        f"gives matrix value {key!r}, which the leg never reads"
        for key in sorted(set(pairing.matrix) - read)
    ]
    return resolved, problems


def _flat(chosen: dict[str, object]) -> dict[str, object]:
    """Return a selection's inputs and environment as one mapping."""
    return {
        **typ.cast("dict[str, object]", chosen["with"]),
        **typ.cast("dict[str, object]", chosen["env"]),
    }


def _differing_keys(lane: dict[str, object], publisher: dict[str, object]) -> set[str]:
    """Return the keys on which two flat selections differ once canonicalised."""
    return {
        key
        for key in set(lane) | set(publisher)
        if _canon(lane.get(key, _ABSENT)) != _canon(publisher.get(key, _ABSENT))
    }


def _canon(value: object) -> object:
    """Return a value in the one spelling that decides equality."""
    if value is _ABSENT:
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text.lower() if text.lower() in {"true", "false"} else text


def _cell_violations(pairing: Pairing, lane: Leg) -> list[str]:
    """Require the declared matrix values to name a cell the job's matrix has."""
    if not pairing.matrix:
        return []
    cells = _matrix_cells(lane.job)
    if cells is None:
        return [
            "its job's matrix is not a mapping, so a declared cell cannot be proved"
        ]
    wanted = {key: _canon(value) for key, value in pairing.matrix.items()}
    if any(
        all(_canon(cell.get(k, _ABSENT)) == v for k, v in wanted.items())
        for cell in cells
    ):
        return []
    return [f"its job's matrix has no cell with {pairing.matrix}"]


def _matrix_cells(job: dict[str, object]) -> list[dict[str, object]] | None:
    """Return the cells a job's matrix can produce, or None when unreadable.

    Axes multiply, and each `include` entry either extends the cells it
    matches or stands as a cell of its own. Both are kept, so a declared
    cell is refused only when nothing could have produced it.
    """
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return None
    axes = {
        key: value
        for key, value in matrix.items()
        if key not in _MATRIX_CONTROLS and isinstance(value, list)
    }
    base = (
        [
            dict(zip(axes, combination, strict=True))
            for combination in itertools.product(*axes.values())
        ]
        if axes
        else []
    )
    includes = [item for item in matrix.get("include", []) if isinstance(item, dict)]
    extended = [
        {**cell, **item}
        for cell in base
        for item in includes
        if all(
            cell.get(key, value) == value for key, value in item.items() if key in axes
        )
    ]
    return [*base, *extended, *includes]


def _guard_violations(pairing: Pairing, lane: Leg) -> list[str]:
    """Require the leg's `if:` to be the pull-request guard and the declared guards."""
    if not pairing.guards:
        return []
    try:
        held = (
            frozenset(conjuncts(lane.step["if"])) if "if" in lane.step else frozenset()
        )
        declared = frozenset(
            term for guard in pairing.guards for term in conjuncts(guard)
        )
    except ConditionError as error:
        return [str(error)]
    expected = declared | {PULL_REQUEST_TERM}
    if held == expected:
        return []
    return [f"its condition is {sorted(held)}, not the declared {sorted(expected)}"]


def guards_by_leg(pairings: cabc.Sequence[Pairing]) -> dict[str, frozenset[str]]:
    """Return each paired lane leg's declared guard terms, normalised.

    Parameters
    ----------
    pairings : Sequence[Pairing]
        The declared pairings.

    Returns
    -------
    dict[str, frozenset[str]]
        The extra `&&` terms each paired lane leg may carry.

    """
    guards: dict[str, frozenset[str]] = {}
    for pairing in pairings:
        try:
            guards[pairing.lane] = frozenset(
                term for guard in pairing.guards for term in conjuncts(guard)
            )
        except ConditionError:
            guards[pairing.lane] = frozenset()
    return guards
