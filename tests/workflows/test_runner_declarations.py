"""Contract that no runner declaration carries a line break.

A folded `runs-on` whose continuation is indented deeper than its first
line keeps the break, and GitHub evaluates the expression regardless, so
a green run says nothing. This module reads every value that can select
a runner: `runs-on` itself and the matrix dimensions it references. It
sits beside `test_runner_placement.py`, which owns which label a job
declares; this one owns only whether the declaration is one line.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    import collections.abc as cabc


#: Any `matrix.<dimension>` reference inside a `runs-on` value, whether
#: the value is that reference alone or a fork selector using it.
_MATRIX_DIMENSION: typ.Final[re.Pattern[str]] = re.compile(
    r"\bmatrix(?:\.([A-Za-z_][A-Za-z0-9_-]*)|\[\s*(['\"])([A-Za-z_][A-Za-z0-9_-]*)\2\s*\])"
)


def _dimensions(values: cabc.Iterable[str]) -> frozenset[str]:
    """Return every matrix dimension *values* reference, in either syntax."""
    return frozenset(
        dotted or bracketed
        for value in values
        for dotted, _, bracketed in _MATRIX_DIMENSION.findall(value)
    )


def _runs_on_strings(runs_on: object) -> cabc.Iterator[tuple[str, object]]:
    """Yield each labelled value in *runs-on*, in any of its three forms.

    A scalar, a sequence of labels, or a `group`/`labels` mapping. Any
    other shape is refused: reading it as "declares no runner" would let
    a broken value through every rule that asks.
    """
    match runs_on:
        case str():
            yield "runs-on", runs_on
        case list():
            yield from ((f"runs-on[{i}]", item) for i, item in enumerate(runs_on))
        case {"group": _} | {"labels": _}:
            yield from _runs_on_mapping(runs_on)
        case _:
            msg = f"runs-on is neither a label, a list nor a group mapping: {runs_on!r}"
            raise TypeError(msg)


def _runs_on_mapping(
    runs_on: cabc.Mapping[str, object],
) -> cabc.Iterator[tuple[str, object]]:
    """Yield the `group` and each of the `labels` of a mapping `runs-on`."""
    if "group" in runs_on:
        yield "runs-on.group", runs_on["group"]
    labels = runs_on.get("labels")
    items = labels if isinstance(labels, list) else [labels] if labels else []
    yield from ((f"runs-on.labels[{i}]", item) for i, item in enumerate(items))


def _include_entries(
    include: object, dimensions: frozenset[str]
) -> cabc.Iterator[tuple[str, object]]:
    """Yield each labelled value an `include` list declares for *dimensions*."""
    if not isinstance(include, list):
        return
    yield from (
        (f"include[{index}].{key}", entry[key])
        for index, entry in enumerate(include)
        if isinstance(entry, dict)
        for key in dimensions & entry.keys()
    )


def _dimension_entries(key: str, value: object) -> cabc.Iterator[tuple[str, object]]:
    """Yield each labelled value of one plain matrix dimension."""
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        yield f"{key}[{index}]", item


def _matrix_entries(
    matrix: object, dimensions: frozenset[str]
) -> cabc.Iterator[tuple[str, object]]:
    """Yield each labelled value a matrix declares for *dimensions*."""
    if not isinstance(matrix, dict):
        return
    yield from _include_entries(matrix.get("include"), dimensions)
    for key, value in matrix.items():
        if key != "include" and key in dimensions:
            yield from _dimension_entries(key, value)


def _runner_declarations(job: reading.JobBody) -> dict[str, str]:
    """Return every string *job* declares that can select a runner.

    That is every string in `runs-on`, in whichever form it takes, and the
    matrix dimensions those strings reference, and nothing else: a
    multi-line value in a dimension no runner reads, such as a script
    body, selects no runner and is not this rule's business.
    """
    own = dict(_runs_on_strings(job["runs-on"])) if "runs-on" in job else {}
    dimensions = _dimensions(value for value in own.values() if isinstance(value, str))
    strategy = job.get("strategy") or {}
    declarations = {**dict(_matrix_entries(strategy.get("matrix"), dimensions)), **own}
    return {
        where: value for where, value in declarations.items() if isinstance(value, str)
    }


class TestRunnerDeclarationParsing:
    """A folded `runs-on` expression must parse to a single line."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_no_runner_declaration_carries_a_line_break(
        self, workflow: str, job_id: str
    ) -> None:
        """A folded `runs-on` expression parses to one line.

        A continuation indented deeper than its first line keeps its line
        break through YAML's folding, so the parsed value holds a newline in
        the middle of a `${{ }}` expression. GitHub evaluates it anyway, so
        a green run is no evidence; the value is read from the parsed
        document here instead. All twenty-five fork fallbacks on this branch
        were written that way.
        """
        job = dict(reading.jobs(workflow))[job_id]
        declarations = _runner_declarations(job)
        offenders = sorted(
            where for where, value in declarations.items() if "\n" in value
        )
        assert not offenders, (
            f"{reading.identifier(workflow, job_id)} declares {offenders} "
            "with an embedded line break; keep a folded scalar's continuation "
            "at the same indent as its first line, or the break survives into "
            "the value"
        )


class TestWhichValuesAreDeclarations:
    """Which values `_runner_declarations` hands to the line-break rule."""

    def test_only_the_referenced_dimension_is_read(self) -> None:
        """A multi-line value in a dimension `runs-on` never reads is ignored.

        Both halves matter: reading every dimension fails a job over a
        script body, and reading none lets a broken label through.
        """
        job: reading.JobBody = {
            "runs-on": "${{ matrix.runner }}",
            "strategy": {
                "matrix": {
                    "runner": ["ubicloud-standard-2"],
                    "script": ["make lint\nmake test"],
                    "include": [
                        {"runner": "windows-latest", "script": "a\nb"},
                    ],
                }
            },
        }

        assert _runner_declarations(job) == {
            "runs-on": "${{ matrix.runner }}",
            "runner[0]": "ubicloud-standard-2",
            "include[0].runner": "windows-latest",
        }

    def test_a_dimension_inside_a_fork_selector_is_read(self) -> None:
        """A dimension named inside a larger expression still counts."""
        job: reading.JobBody = {
            "runs-on": (
                "${{ github.event.pull_request.head.repo.fork "
                "&& 'ubuntu-latest' || matrix.os }}"
            ),
            "strategy": {"matrix": {"os": ["ubicloud-standard-2\n"]}},
        }

        declarations = _runner_declarations(job)

        assert "os[0]" in declarations, (
            "a fork selector naming matrix.os references the `os` dimension, "
            f"but its entries were not read; read as {declarations!r}"
        )

    @pytest.mark.parametrize(
        ("runs_on", "expected"),
        [
            pytest.param(
                ["self-hosted", "${{ matrix.os }}"],
                {"runs-on[0]": "self-hosted", "runs-on[1]": "${{ matrix.os }}"},
                id="sequence",
            ),
            pytest.param(
                {"group": "linux\n", "labels": ["${{ matrix.os }}"]},
                {"runs-on.group": "linux\n", "runs-on.labels[0]": "${{ matrix.os }}"},
                id="group-mapping",
            ),
            pytest.param(
                {"labels": "${{ matrix.os }}"},
                {"runs-on.labels[0]": "${{ matrix.os }}"},
                id="labels-as-a-scalar",
            ),
        ],
    )
    def test_every_runs_on_form_is_read(
        self, runs_on: object, expected: dict[str, str]
    ) -> None:
        """A sequence and a group mapping are read string by string.

        Dropping a form that is not a scalar would let a line break in it,
        or in the matrix dimension it references, through unseen.
        """
        job: reading.JobBody = {
            "runs-on": runs_on,
            "strategy": {"matrix": {"os": ["ubicloud-standard-2\n"]}},
        }

        declarations = _runner_declarations(job)

        assert declarations == {**expected, "os[0]": "ubicloud-standard-2\n"}, (
            f"runs-on {runs_on!r} should be read as {expected!r} plus the "
            f"matrix dimension it references; read as {declarations!r}"
        )

    @pytest.mark.parametrize(
        "runs_on",
        [
            pytest.param("${{ matrix['os'] }}", id="single-quoted-bracket"),
            pytest.param('${{ matrix["os"] }}', id="double-quoted-bracket"),
        ],
    )
    def test_a_bracketed_dimension_is_read(self, runs_on: str) -> None:
        """`matrix['os']` references the same dimension as `matrix.os`."""
        job: reading.JobBody = {
            "runs-on": runs_on,
            "strategy": {"matrix": {"os": ["ubicloud-standard-2"]}},
        }

        declarations = _runner_declarations(job)

        assert "os[0]" in declarations, (
            f"{runs_on!r} references the `os` dimension, but its entries were "
            f"not read; read as {declarations!r}"
        )

    def test_an_unknown_runs_on_form_is_refused(self) -> None:
        """A `runs-on` in no form GitHub accepts fails instead of reading empty."""
        job: reading.JobBody = {"runs-on": 5}

        with pytest.raises(TypeError, match="runs-on is neither"):
            _runner_declarations(job)
