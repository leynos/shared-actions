r"""Contract that the act lane runs once when it is opted into.

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

Forcing the falsy value needs `export`, so the child pytest process sees
it, and `override`, so a command-line `ACT_WORKFLOW_TESTS=1` cannot beat
it. Neither may ride on the recipe line. An inline `VAR=value` prefix
makes make exec the line through a shell rather than directly, and on
Windows that shell consumes the backslashes in the `UV=C:\...` path the
caller passes, so the binary is not found and the run dies with a
`command not found` naming a path nobody typed. Both keywords therefore
live on the target as make variables, and the recipe line stays a bare
`$(UV)` command. Make 3.81 -- the `/usr/bin/make` macOS ships, and which
parses this file from `test_doctest_target.py` -- cannot read the two
keywords on one target-specific line at all, so `export` is a global
directive and `override` stays on the target.

What is testable here is the Makefile as text: which target carries which
value, whether that value reaches the child, and whether the recipe line
is still bare. Executed behaviour belongs to `test_doctest_target.py`,
which runs make against a copy of the Makefile with a stub `uv`.
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
PLAIN_TARGET: typ.Final[str] = "test"

#: `VAR=value` at the head of a command. An assignment anywhere else in the
#: line is an argument, not an environment override, so the match is anchored
#: there. Its presence at the head of a recipe is the Windows defect: make
#: hands such a line to a shell, and the shell eats the `UV=C:\\...` path.
_ASSIGNMENT: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s]*)"
)

#: A target-specific assignment: `target: keyword... NAME op value`. The name
#: class excludes `-`, so `test: test-act` and `test: .venv doctest` cannot be
#: read as assignments to a variable.
_TARGET_ASSIGNMENT: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?P<target>[A-Za-z0-9_-]+):[ \t]+"
    r"(?P<keywords>(?:(?:export|override|private)[ \t]+)*)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*(?P<operator>[:?+]?=)[ \t]*(?P<value>.*)$"
)

#: A bare `export VAR` directive, which is how the gate reaches the child
#: without riding on the recipe line.
_EXPORT_DIRECTIVE: typ.Final[re.Pattern[str]] = re.compile(
    rf"^export[ \t]+{GATE_VARIABLE}[ \t]*$"
)


class Gate(typ.NamedTuple):
    """The value a target forces into the gate, and how it says so."""

    value: str
    keywords: frozenset[str]


def _document() -> list[str]:
    """Return the Makefile's lines."""
    return MAKEFILE.read_text(encoding="utf-8").splitlines()


def _recipe(target: str) -> str:
    """Return the shell script of *target*'s recipe.

    The recipe's first line only. Every target here builds its command
    there, and following continuations would mean re-implementing make's
    line joining to read a command that is already on one line.

    The line immediately after the target, not the next indented line
    anywhere below it. `test` is defined several times: with its
    target-specific gate, with its recipe, and bare as `test: test-act` to
    attach the lane as a prerequisite. Scanning forward for indentation
    would skip the definitions that carry no recipe; the loop therefore
    keeps looking until it finds the definition whose successor is
    indented, which is the one that owns a recipe.
    """
    document = _document()
    header = f"{target}:"
    for number, line in enumerate(document):
        if not line.startswith(header):
            continue
        candidate = number + 1
        if candidate < len(document) and document[candidate].startswith("\t"):
            return document[candidate].strip()
    pytest.fail(f"the Makefile defines no {target!r} target with a recipe")


def _gate(target: str) -> Gate | None:
    """Return the gate assignment *target* makes for itself, if any."""
    for line in _document():
        match = _TARGET_ASSIGNMENT.match(line)
        if match is None or match["name"] != GATE_VARIABLE:
            continue
        if match["target"] == target:
            return Gate(
                value=match["value"].strip(),
                keywords=frozenset(match["keywords"].split()),
            )
    return None


def _gate_is_exported(target: str) -> bool:
    """Return whether *target*'s gate value reaches the child process.

    A target-specific `export` says so on the target's own line; the global
    directive says it for every target. Either is enough, and both have to
    be accepted: the target line cannot carry `export` where make 3.81 is
    the parser.
    """
    gate = _gate(target)
    if gate is not None and "export" in gate.keywords:
        return True
    return any(_EXPORT_DIRECTIVE.match(line) for line in _document())


def test_the_plain_target_forces_the_gate_off_and_exports_it() -> None:
    """The plain suite asserts the gate off, and the child can see it.

    Without the forced value, `make test` lands in this very module through
    the default `testpaths` and runs the suite a second time, which is why
    the assertion is about the command line rather than about anything
    executed. Without `override`, a command-line `ACT_WORKFLOW_TESTS=1`
    beats the assignment and the same second run returns.
    """
    gate = _gate(PLAIN_TARGET)

    assert gate is not None, (
        f"the plain `{PLAIN_TARGET}` target does not force {GATE_VARIABLE}, so "
        "the opt-in is inherited from the caller and the default run either "
        "repeats the act lane or runs it without the lane"
    )
    assert "override" in gate.keywords, (
        f"`{PLAIN_TARGET}` sets {GATE_VARIABLE}={gate.value!r} without "
        "`override`, so `make test ACT_WORKFLOW_TESTS=1` from the command "
        "line beats it and the plain run un-skips the lane's modules"
    )
    assert not coerce_bool(gate.value, default=True), (
        f"the plain `{PLAIN_TARGET}` target sets {GATE_VARIABLE}="
        f"{gate.value!r}, which the pytest-side gate reads as opted in; the "
        "plain run must assert the gate is off"
    )
    assert _gate_is_exported(PLAIN_TARGET), (
        f"{GATE_VARIABLE} is forced off on `{PLAIN_TARGET}` but never "
        "exported, so the child pytest process does not see it and still "
        "inherits the caller's opt-in"
    )


def test_the_lane_target_opts_in_and_keeps_the_whole_directory() -> None:
    """`test-act` is the one place the gate is turned on.

    It is also the only recipe scoped to the whole directory. Once the
    plain run asserts the gate off, a lane that ran anything narrower
    would leave the rest of `tests/workflows` running nowhere.
    """
    gate = _gate(LANE_TARGET)
    recipe = _recipe(LANE_TARGET)

    assert gate is not None, f"{LANE_TARGET} does not force a truthy {GATE_VARIABLE}"
    assert coerce_bool(gate.value, default=False), (
        f"{LANE_TARGET} sets {GATE_VARIABLE}={gate.value!r}, which the "
        "pytest-side gate reads as opted out, so the lane's modules skip and "
        "the lane reports success having run nothing"
    )
    assert _gate_is_exported(LANE_TARGET), (
        f"{LANE_TARGET} forces {GATE_VARIABLE} on but never exports it, so "
        "the child pytest process does not see the opt-in and every module "
        "skips"
    )
    assert "tests/workflows" in shlex.split(recipe), (
        f"{LANE_TARGET} no longer runs the whole tests/workflows directory; "
        "with the plain run gated off, the modules it stops naming would run "
        "nowhere"
    )


def test_the_two_targets_force_opposite_gates() -> None:
    """The gates in the two targets never agree, in either direction.

    Two separate assertions can both pass while the pair contradicts: a
    plain run forcing off and a lane forcing off, or both forcing on. Only
    the pair shows which run owns the lane.
    """
    gates = {
        target: coerce_bool(
            (_gate(target) or Gate("", frozenset())).value, default=False
        )
        for target in (PLAIN_TARGET, LANE_TARGET)
    }

    assert gates[PLAIN_TARGET] is False, gates
    assert gates[LANE_TARGET] is True, gates


def test_the_recipe_lines_stay_bare() -> None:
    r"""A leading `VAR=value` on a recipe hands the line to a shell.

    Make execs a bare command directly; a line that starts with an inline
    assignment has to go through `/bin/sh` first, and on Windows that shell
    eats the backslashes out of the `UV=C:\...` path a caller passes, so
    the binary is not found. The gate belongs on the target for that
    reason, and this holds it there.
    """
    for target in (PLAIN_TARGET, LANE_TARGET):
        recipe = _recipe(target)
        head = shlex.split(recipe)[0]
        assert _ASSIGNMENT.match(head) is None, (
            f"the `{target}` recipe begins with the inline assignment "
            f"{head!r}; make will run the line through a shell, and on "
            "Windows the shell consumes the backslashes in a `UV=C:\\...` "
            "path. Set the variable on the target instead"
        )


def test_no_target_line_combines_export_and_override() -> None:
    """Make 3.81 cannot parse `target: export override VAR := ...`.

    It reports "multiple target patterns" and parses nothing. macOS ships
    3.81 as `/usr/bin/make`, and `test_doctest_target.py` runs make over
    this file there, so the combination would break the plain suite on that
    platform -- a fix for one platform's parsing bug causing another's.
    The two keywords have to be split between a global directive and the
    target line.
    """
    offenders = [
        line
        for line in _document()
        if (match := _TARGET_ASSIGNMENT.match(line)) is not None
        and "export" in match["keywords"]
        and "override" in match["keywords"]
    ]

    assert not offenders, (
        "these target-specific assignments combine `export` and `override`, "
        f"which make 3.81 rejects: {offenders}"
    )


def test_the_gate_is_the_one_pytest_reads() -> None:
    """The variable and its spelling are the ones the pytest gate uses.

    The whole rule rests on two spellings meaning the same thing. If the
    pytest side ever reads a different name or a value `coerce_bool`
    rejects, both targets could be internally consistent and still
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
            "targets and the gate disagree about the same spelling"
        )
