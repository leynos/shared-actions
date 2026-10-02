"""Contract that the act lane runs once when it is opted into.

`tests/workflows` is inside the default `testpaths`, and the modules in it
that need act gate themselves on `ACT_WORKFLOW_TESTS`. The plain `make
test` recipe used to export nothing, so the variable was inherited from
the caller. Two consequences followed from that one omission, and both
are silent:

- Opting in with `make test ACT_WORKFLOW_TESTS=1` ran the lane's modules
  twice. `test-act` ran them as a prerequisite, and then the default
  invocation ran them again, because the exported `1` survived into it
  and un-skipped them. The second run is the expensive one, and it reads
  as the lane having run once.

- Calling `make test` from a shell that already had the variable set ran
  the lane's modules *without* the lane. The opt-in reached pytest, so
  the act-dependent modules collected and ran, but `test-act` never did,
  taking its `ACT` export and its whole-directory scope with it. The
  modules ran outside the recipe that owns them, on a machine that may
  not even have act installed.

Exporting the falsy value in the plain recipe closes both. It cannot be
tested here, though: `make test` runs the whole suite in a subprocess and
this module is inside that suite, so the test would be the recursion.

What is testable is the shell command the recipe builds. Whether
`ACT_WORKFLOW_TESTS=0` survives into the pytest process is a question
about the command line, and `shlex` plus the same `coerce_bool` the
pytest-side gate uses answers it directly.
"""

from __future__ import annotations

import re
import shlex
import typing as typ
from pathlib import Path

import pytest

from bool_utils import coerce_bool

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
MAKEFILE: typ.Final[Path] = REPOSITORY_ROOT / "Makefile"

#: The pytest-side opt-in gate and the make-side opt-in, as the Makefile
#: names them.
GATE_VARIABLE: typ.Final[str] = "ACT_WORKFLOW_TESTS"
LANE_TARGET: typ.Final[str] = "test-act"

#: `VAR=value` at the head of a command, which is where the recipe's
#: override sits. An assignment anywhere else in the line is an argument,
#: not an environment override, so the match is anchored there.
_ASSIGNMENT: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s]*)"
)


def _recipe(target: str) -> str:
    """Return the shell script of *target*'s recipe.

    The recipe's first line only. Every target here builds its command
    there, and following continuations would mean re-implementing make's
    line joining to read a command that is already on one line.

    The line immediately after the target, not the next indented line
    anywhere below it. `test` is defined twice: once with the recipe and
    once bare, as `test: test-act`, to attach the lane as a prerequisite.
    Scanning forward for indentation would skip the bare definition and
    return whichever recipe came next -- `test-act`'s, once the pair was
    ordered the other way. Matching the definition that owns a recipe
    keeps the two apart whatever order they appear in.
    """
    document = MAKEFILE.read_text(encoding="utf-8").splitlines()
    header = f"{target}:"
    for number, line in enumerate(document):
        if not line.startswith(header):
            continue
        candidate = number + 1
        if candidate < len(document) and document[candidate].startswith("\t"):
            return document[candidate].strip()
    pytest.fail(f"the Makefile defines no {target!r} target with a recipe")


def _environment_overrides(script: str) -> dict[str, str]:
    """Return the `VAR=value` overrides the command in *script* carries.

    Parsed with `shlex` so a value containing a quoted space is not split,
    and read only from the command's own words so that a `VAR=value`
    appearing as an argument is not mistaken for an override.
    """
    overrides: dict[str, str] = {}
    for word in shlex.split(script):
        if _ASSIGNMENT.match(word) is None:
            break
        name, _, value = word.partition("=")
        overrides[name] = value
    return overrides


@pytest.fixture(scope="module")
def plain_recipe() -> str:
    """Return the recipe of the plain `test` target."""
    return _recipe("test")


@pytest.fixture(scope="module")
def lane_recipe() -> str:
    """Return the recipe of the `test-act` target."""
    return _recipe(LANE_TARGET)


def test_the_plain_recipe_asserts_the_gate_is_off(plain_recipe: str) -> None:
    """The plain suite exports the gate off rather than inheriting it.

    Without this, `make test` lands in this very module through the
    default `testpaths` and runs the suite a second time, which is why
    the assertion is about the command line rather than about anything
    executed.
    """
    overrides = _environment_overrides(plain_recipe)

    assert GATE_VARIABLE in overrides, (
        f"the plain `test` recipe does not export {GATE_VARIABLE}, so the "
        "opt-in is inherited from the caller and the default run either "
        "repeats the act lane or runs it without the lane"
    )
    assert not coerce_bool(overrides[GATE_VARIABLE], default=True), (
        f"the plain `test` recipe sets {GATE_VARIABLE}="
        f"{overrides[GATE_VARIABLE]!r}, which the pytest-side gate reads as "
        "opted in; the plain run must assert the gate is off"
    )


def test_the_lane_recipe_opts_in_and_keeps_the_whole_directory(
    lane_recipe: str,
) -> None:
    """`test-act` is the one place the gate is turned on.

    It is also the only recipe scoped to the whole directory. Once the
    plain run asserts the gate off, a lane that ran anything narrower
    would leave the rest of `tests/workflows` running nowhere.
    """
    overrides = _environment_overrides(lane_recipe)

    assert coerce_bool(overrides.get(GATE_VARIABLE, ""), default=False), (
        f"{LANE_TARGET} does not export a truthy {GATE_VARIABLE}, so its "
        "modules skip and the lane reports success having run nothing"
    )
    assert "tests/workflows" in shlex.split(lane_recipe), (
        f"{LANE_TARGET} no longer runs the whole tests/workflows directory; "
        "with the plain run gated off, the modules it stops naming would run "
        "nowhere"
    )


def test_the_two_recipes_export_opposite_gates(
    plain_recipe: str,
    lane_recipe: str,
) -> None:
    """The gates in the two recipes never agree, in either direction.

    Two separate assertions can both pass while the pair contradicts: a
    plain run exporting off and a lane exporting off, or both exporting
    on. Only the pair shows which run owns the lane.
    """
    gates = {
        "test": coerce_bool(
            _environment_overrides(plain_recipe).get(GATE_VARIABLE, ""),
            default=False,
        ),
        LANE_TARGET: coerce_bool(
            _environment_overrides(lane_recipe).get(GATE_VARIABLE, ""),
            default=False,
        ),
    }

    assert gates["test"] is False, gates
    assert gates[LANE_TARGET] is True, gates


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param("ACT_WORKFLOW_TESTS=0 pytest", "0", id="bare"),
        pytest.param("ACT_WORKFLOW_TESTS=false pytest", "false", id="word"),
        pytest.param("A=1 ACT_WORKFLOW_TESTS=0 pytest", "0", id="second"),
        pytest.param(
            "ACT_WORKFLOW_TESTS=0 uv run pytest -n auto",
            "0",
            id="before-a-long-command",
        ),
        pytest.param("pytest -k ACT_WORKFLOW_TESTS=0", None, id="an-argument"),
        pytest.param("pytest", None, id="none"),
    ],
)
def test_only_leading_words_are_read_as_overrides(
    script: str,
    expected: str | None,
) -> None:
    """A `VAR=value` argument is not an override, and a leading one is.

    The Makefile is text here, so the parse has to fail closed. A
    pattern searching the whole line would find the gate in a pytest
    argument or a `-k` expression and read the plain run as opted in.
    """
    overrides = _environment_overrides(script)

    assert overrides.get(GATE_VARIABLE) == expected, (
        f"{script!r} should read {GATE_VARIABLE} as {expected!r}; got "
        f"{overrides.get(GATE_VARIABLE)!r}"
    )


def test_the_gate_is_the_one_pytest_reads() -> None:
    """The variable and its spelling are the ones the pytest gate uses.

    The whole rule rests on two spellings meaning the same thing. If the
    pytest side ever reads a different name or a value `coerce_bool`
    rejects, both recipes could be internally consistent and still
    disconnect, with the lane running in the plain suite again.
    """
    source = (REPOSITORY_ROOT / "tests" / "workflows" / "conftest.py").read_text(
        encoding="utf-8"
    )

    assert f'"{GATE_VARIABLE}"' in source, (
        f"tests/workflows/conftest.py no longer reads {GATE_VARIABLE}; the "
        "Makefile is exporting a variable nothing consults"
    )
    for value, expected in (("0", False), ("1", True)):
        assert coerce_bool(value, default=False) is expected, (
            f"coerce_bool reads {value!r} as the wrong truth value, so the "
            "recipes and the gate disagree about the same spelling"
        )
