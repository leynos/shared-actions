"""Property test of the docstring-reachability model against `DocTestFinder`.

`test_doctest_coverage._docstrings` is a static model of which docstrings
`doctest.DocTestFinder` can reach: it walks classes and module-level
definitions, never enters a function body, drops a definition that a later
one of the same name replaces, prunes constant `if` arms, and keeps both
arms of a condition it cannot decide. The fixed cases in that module pin
each rule once. This module checks the rules together, on generated
modules, with the finder itself as the oracle.

The invariant has two halves because the model is deliberately permissive:

- With no undecidable condition, the model and the finder agree exactly.
- With one, the finder runs a single arm, so it can only find fewer
  examples than the model, and what the model keeps is at most what the
  runs of the module find between them, one run per way of setting its
  independent flags.
"""

from __future__ import annotations

import ast
import doctest
import itertools
import re
import typing as typ

from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from . import test_doctest_coverage as coverage

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The placeholder an undecidable condition tests. Rendering numbers each
#: one into its own module-level name (`F0`, `F1`, ...), because two
#: conditions on one name are correlated and could make an arm infeasible,
#: which the model cannot know and the upper bound must not assume. The
#: model cannot decide any of them; each run of the oracle fixes them all.
_FLAG: typ.Final[str] = "FLAG"

#: The most independent flags one generated module may carry, which bounds
#: the oracle to 2**3 runs per module.
_MAX_FLAGS: typ.Final[int] = 3

#: Names definitions draw from. Few on purpose, so the same name is bound
#: more than once and the "last binding wins" rule is exercised.
_NAMES: typ.Final = st.sampled_from(["a", "b", "c"])

#: How deeply scopes and conditions nest.
_MAX_DEPTH: typ.Final[int] = 3

_INDENT: typ.Final[str] = "    "


type _Statement = tuple[typ.Any, ...]


@st.composite
def _definition(
    draw: st.DrawFn, kind: str, depth: int, *, undecidable: bool
) -> _Statement:
    """Draw a `def` or `class` statement and the scope it opens."""
    body = draw(_block(depth + 1, undecidable=undecidable))
    return (kind, draw(_NAMES), body)


@st.composite
def _conditional(
    draw: st.DrawFn, kind: str, depth: int, *, undecidable: bool
) -> _Statement:
    """Draw an `if` whose condition is constant or an undecidable flag."""
    condition = draw(st.booleans()) if kind == "const_if" else _FLAG
    then = draw(_block(depth + 1, undecidable=undecidable))
    otherwise = draw(_block(depth + 1, undecidable=undecidable))
    return ("if", condition, then, otherwise)


@st.composite
def _statement(draw: st.DrawFn, depth: int, *, undecidable: bool) -> _Statement:
    """Draw one statement of any kind this grammar allows."""
    kinds = ["def", "class", "const_if"] + (["flag_if"] if undecidable else [])
    kind = draw(st.sampled_from(kinds))
    drawn = _definition if kind in {"def", "class"} else _conditional
    return draw(drawn(kind, depth, undecidable=undecidable))


@st.composite
def _block(draw: st.DrawFn, depth: int, *, undecidable: bool) -> list[_Statement]:
    """Draw a statement list, as tuples a renderer turns into source."""
    count = 0 if depth >= _MAX_DEPTH else draw(st.integers(min_value=0, max_value=3))
    return [draw(_statement(depth, undecidable=undecidable)) for _ in range(count)]


def _arm(
    block: list[_Statement], level: int, flags: list[str], serials: list[int]
) -> list[str]:
    """Render one arm of an `if`, which Python needs to be non-empty."""
    return _render(block, level, flags, serials) or [f"{_INDENT * level}pass"]


def _render_definition(
    statement: _Statement, level: int, flags: list[str], serials: list[int]
) -> list[str]:
    """Render a `def` or `class` with a serial-numbered docstring."""
    kind, name, body = statement
    pad = _INDENT * level
    header = f"def {name}():" if kind == "def" else f"class {name}:"
    serials.append(len(serials))
    return [
        f"{pad}{header}",
        f"{pad}{_INDENT}'''Documented {serials[-1]}.",
        f"{pad}{_INDENT}{coverage._PROBE_PROMPT} None",
        f"{pad}{_INDENT}'''",
        *_render(body, level + 1, flags, serials),
    ]


def _render_conditional(
    statement: _Statement, level: int, flags: list[str], serials: list[int]
) -> list[str]:
    """Render an `if`, giving each undecidable condition its own flag name."""
    _, condition, then, otherwise = statement
    pad = _INDENT * level
    if condition == _FLAG:
        flags.append(f"F{len(flags)}")
    name = flags[-1] if condition == _FLAG else condition
    return [
        f"{pad}if {name}:",
        *_arm(then, level + 1, flags, serials),
        f"{pad}else:",
        *_arm(otherwise, level + 1, flags, serials),
    ]


def _render(
    block: list[_Statement],
    level: int,
    flags: list[str],
    serials: list[int],
) -> list[str]:
    """Render *block* as source lines indented *level* steps.

    *flags* collects the name given to each undecidable condition, in the
    order rendered, so the caller knows what to bind. Every definition gets
    a serial number in its docstring, so which definitions a run reached can
    be told apart even when their text would otherwise match.
    """
    lines: list[str] = []
    for statement in block:
        renderer = (
            _render_definition
            if statement[0] in {"def", "class"}
            else _render_conditional
        )
        lines += renderer(statement, level, flags, serials)
    return lines


def _flag_count(block: list[_Statement]) -> int:
    """Return how many undecidable conditions *block* renders."""
    flags: list[str] = []
    _render(block, 0, flags, [])
    return len(flags)


def _source(block: list[_Statement], assignment: tuple[bool, ...]) -> str:
    """Return a module's source with each flag fixed by *assignment*."""
    flags: list[str] = []
    body = _render(block, 0, flags, [])
    bindings = [
        f"{name} = {value}" for name, value in zip(flags, assignment, strict=True)
    ]
    return coverage._probe_source("\n".join([*bindings, *body]) + "\n")


def _assignments(block: list[_Statement]) -> list[tuple[bool, ...]]:
    """Return every way of setting the flags *block* renders."""
    return list(itertools.product([True, False], repeat=_flag_count(block)))


_SERIAL: typ.Final[re.Pattern[str]] = re.compile(r"Documented (\d+)\.")


def _serials(docstrings: cabc.Iterable[str]) -> list[int]:
    """Return the serial of each docstring in *docstrings* that holds an example."""
    return sorted(
        int(match.group(1))
        for text in docstrings
        if coverage._PROMPT.search(text) and (match := _SERIAL.search(text))
    )


def _modelled(source: str) -> list[int]:
    """Return the serials the reachability model says the finder reaches."""
    return _serials(coverage._docstrings(ast.parse(source)))


def _found(source: str) -> list[int]:
    """Return the serials `DocTestFinder` reaches when *source* is run."""
    with coverage._probe_module(source) as module:
        tests = doctest.DocTestFinder().find(module, name=coverage._PROBE_MODULE)
    return _serials(test.docstring for test in tests if test.examples)


_SETTINGS = settings(
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@_SETTINGS
@given(_block(0, undecidable=False))
def test_model_equals_the_finder_when_every_condition_is_decidable(
    block: list[_Statement],
) -> None:
    """With only constant conditions, the model and the finder agree exactly.

    Compared by serial rather than by count, so a model that reached the
    wrong definitions but the right number of them still fails.
    """
    source = _source(block, ())
    ast.parse(source)

    assert _modelled(source) == _found(source), source


@_SETTINGS
# Found by this property: an arm that leaves an earlier definition of `c`
# alone and a nested condition that rebinds it handed the merge the same
# entry twice, so the model counted one definition as three. Kept as a
# fixed example so the regression does not depend on the search finding it.
@example(
    block=[
        ("def", "c", []),
        ("if", "FLAG", [], [("if", "FLAG", [], [("def", "c", [])])]),
    ]
)
@given(_block(0, undecidable=True))
def test_model_is_the_union_of_the_runs_when_a_condition_is_undecidable(
    block: list[_Statement],
) -> None:
    """The model counts each definition any run reaches, once, and no other.

    The finder follows one arm of each condition, so a single run reaches
    a subset of what the model keeps. Across every setting of the
    independent flags, the runs between them reach exactly the definitions
    the model keeps, and the model lists none twice: a duplicate would make
    the coverage rule fail on a file that is in order.
    """
    assume(_flag_count(block) <= _MAX_FLAGS)
    assignments = _assignments(block)
    source = _source(block, assignments[0])
    modelled = _modelled(source)
    reached = set().union(
        *(set(_found(_source(block, assignment))) for assignment in assignments)
    )

    assert len(modelled) == len(set(modelled)), source
    assert set(modelled) == reached, source
