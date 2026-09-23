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
    r"\bmatrix\.([A-Za-z_][A-Za-z0-9_-]*)"
)


def _include_entries(
    include: object, dimensions: frozenset[str]
) -> cabc.Iterator[tuple[str, object]]:
    """Yield each labelled value an `include` list declares for *dimensions*."""
    if not isinstance(include, list):
        return
    for index, entry in enumerate(include):
        if isinstance(entry, dict):
            for key, value in entry.items():
                if key in dimensions:
                    yield f"include[{index}].{key}", value


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

    That is `runs-on` itself and the matrix dimensions it references, and
    nothing else: a multi-line value in a dimension no runner reads, such
    as a script body, selects no runner and is not this rule's business.
    """
    runs_on = job.get("runs-on")
    dimensions = frozenset(
        _MATRIX_DIMENSION.findall(runs_on) if isinstance(runs_on, str) else ()
    )
    strategy = job.get("strategy") or {}
    declarations = dict(_matrix_entries(strategy.get("matrix"), dimensions))
    if "runs-on" in job:
        declarations["runs-on"] = runs_on
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

        assert "os[0]" in _runner_declarations(job)
