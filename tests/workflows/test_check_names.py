"""Contract for a job's check name staying stable across placements.

A required status check is matched by name. A name carrying the
runner, whether interpolated from the matrix dimension that supplies
`runs-on` or written out as a label, changes the moment the placement
does, and every required context naming it stops reporting. That is
what blocked this branch once already: five required contexts named
`ubuntu-latest` lanes that had become Ubicloud ones. This module is the
contract that keeps a check name from depending on where the job ran.

It draws on the same matrix reading `test_runner_placement.py` uses to
decide a job's runner, because a name is judged against the one matrix
dimension that actually supplies `runs-on`, and a same-named but
unrelated dimension must not be flagged.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _workflow_reading as reading

#: Every `${{ ... }}` interpolation in a string, body captured.
_INTERPOLATION: typ.Final[re.Pattern[str]] = re.compile(r"\$\{\{(?P<body>.*?)\}\}")

#: A `matrix.<key>` reference inside an interpolation.
_MATRIX_KEY_REFERENCE: typ.Final[re.Pattern[str]] = re.compile(
    r"\bmatrix\.([A-Za-z_][A-Za-z0-9_-]*)"
)

#: Any `runner.*` context reference. A job name is evaluated before a
#: runner is assigned, so such a reference cannot mean what it looks
#: like; naming it here refuses the shape rather than the outcome.
_RUNNER_CONTEXT_REFERENCE: typ.Final[re.Pattern[str]] = re.compile(r"\brunner\.")


def _runner_matrix_key(job: reading.JobBody) -> str | None:
    """Return the matrix key *job*'s `runs-on` defers to, if it defers."""
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str):
        return None
    match = reading.MATRIX_REFERENCE.match(runs_on)
    return match.group(1) if match else None


def _check_name(job_id: str, job: reading.JobBody) -> str:
    """Return the name GitHub reports *job* under, or the job id."""
    declared = job.get("name")
    return declared if isinstance(declared, str) else job_id


def _name_offences(name: str, runner_key: str | None) -> list[str]:
    """Return every way *name* lets a runner decide what the job is called."""
    recognized = reading.RECOGNIZED_LINUX_LABELS | reading.RECOGNIZED_OTHER_LABELS
    offences = [
        f"names the runner label {label!r} literally"
        for label in sorted(recognized)
        if reading.label_token(label).search(name)
    ]
    for interpolation in _INTERPOLATION.finditer(name):
        body = interpolation.group("body")
        if runner_key is not None and runner_key in _MATRIX_KEY_REFERENCE.findall(body):
            offences.append(f"interpolates the runner dimension matrix.{runner_key}")
        if _RUNNER_CONTEXT_REFERENCE.search(body):
            offences.append(f"interpolates a runner context in {body.strip()!r}")
    return offences


class TestMatrixRunnerJobNaming:
    """A job whose runner comes from its matrix must name itself explicitly."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_a_matrix_runner_job_declares_its_own_name(
        self, workflow: str, job_id: str
    ) -> None:
        """A job whose runner comes from its matrix names itself explicitly.

        Without a `name:`, GitHub composes the check name from the matrix
        values, so the runner label ends up in it. Under the fork fallback
        that value is not even constant: the same lane reports as
        `python-tests (ubicloud-standard-2)` for an internal pull request and
        `python-tests (ubuntu-latest)` for a fork's. No required-check list
        can name one lane whose check name depends on who opened the pull
        request, so the ceiling on this is the ruleset, not taste.
        """
        job = dict(reading.jobs(workflow))[job_id]
        runner_key = _runner_matrix_key(job)
        if runner_key is None:
            pytest.skip("runner is not taken from the matrix")
        assert isinstance(job.get("name"), str), (
            f"{reading.identifier(workflow, job_id)} takes its runner from "
            f"matrix.{runner_key} and declares no name, so GitHub will compose "
            "its check name from the matrix values, runner label included; "
            "give it a name keyed on a platform word instead"
        )


class TestJobNameStability:
    """A check name must be stable whatever runner the job lands on."""

    @pytest.mark.parametrize(("workflow", "job_id"), reading.runner_job_ids())
    def test_no_job_name_interpolates_its_runner(
        self, workflow: str, job_id: str
    ) -> None:
        """A check name is stable whatever runner the job lands on.

        A required status check is matched by name. A name carrying the
        runner, whether interpolated from the matrix dimension that supplies
        `runs-on` or written out as a label, changes the moment the
        placement does, and every required context naming it stops
        reporting. That is what blocked this branch: five required contexts
        named `ubuntu-latest` lanes that had become Ubicloud ones.
        """
        job = dict(reading.jobs(workflow))[job_id]
        name = _check_name(job_id, job)
        offences = _name_offences(name, _runner_matrix_key(job))
        assert not offences, (
            f"{reading.identifier(workflow, job_id)} reports as {name!r}, "
            f"which {'; '.join(offences)}; name the lane by platform word so "
            "the check name survives a change of runner"
        )


class TestNameOffenceReader:
    """The name reader must be narrow as well as sufficient."""

    @pytest.mark.parametrize(
        ("name", "runner_key", "expected"),
        [
            pytest.param(
                "python-tests (${{ matrix.platform }})", "os", [], id="platform"
            ),
            pytest.param("build-release", "runner", [], id="bare"),
            pytest.param(
                "build-release (${{ matrix.platform }}, ${{ matrix.target }})",
                "runner",
                [],
                id="platform-and-target",
            ),
            pytest.param(
                "build-release (${{ matrix.target }})",
                "runner",
                [],
                id="a-non-runner-dimension-is-fine",
            ),
            pytest.param(
                "python-tests (${{ matrix.os }})",
                "os",
                ["interpolates the runner dimension matrix.os"],
                id="the-runner-dimension",
            ),
            pytest.param(
                "python-tests (ubuntu-latest)",
                "os",
                ["names the runner label 'ubuntu-latest' literally"],
                id="a-literal-label",
            ),
            pytest.param(
                "python-tests (${{ runner.os }})",
                "os",
                ["interpolates a runner context in 'runner.os'"],
                id="a-runner-context",
            ),
            pytest.param(
                "build-release (${{ matrix.os }})",
                "runner",
                [],
                id="a-same-named-dimension-that-is-not-the-runner",
            ),
            pytest.param(
                "python-tests (ubuntu-latest-arm64)",
                "os",
                [],
                id="a-longer-label-merely-containing-one",
            ),
        ],
    )
    def test_the_name_reader_is_narrow_as_well_as_sufficient(
        self, name: str, runner_key: str | None, expected: list[str]
    ) -> None:
        """The reader refuses a leaked runner and accepts a platform word.

        Both directions are asserted, because a reader that refused every
        interpolation would refuse `build-release (..., matrix.target)`,
        which is exactly the name this repository needs, and a reader that
        matched `ubuntu-latest` as a substring would refuse a hypothetical
        `ubuntu-latest-arm64` lane that merely contains it.
        """
        assert _name_offences(name, runner_key) == expected, (
            f"the name {name!r} read against runner dimension {runner_key!r} "
            f"yielded {_name_offences(name, runner_key)}, expected {expected}"
        )
