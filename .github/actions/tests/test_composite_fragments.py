"""Exercise the composite-action fragment harness itself.

The harness stands between a test and the action it exercises, so a defect here
makes a suite report an outcome no runner would produce. These tests pin the
parts that decide what a fragment sees and what it is recorded as producing.
"""

from __future__ import annotations

import subprocess
import typing as typ

import pytest

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    LifecycleResult,
    StepResult,
    parse_outputs,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _context(**overrides: object) -> ActionContext:
    """Return a context with the fields a condition test needs."""
    base: dict[str, object] = {
        "inputs": {"version": "0.5.0"},
        "runner_os": "Linux",
        "runner_arch": "X64",
        "action_path": "/action",
    }
    base.update(overrides)
    return ActionContext(**base)  # ty: ignore[invalid-argument-type]


class TestParseOutputs:
    """Validate how ``$GITHUB_OUTPUT`` lines are read back."""

    def test_reads_single_line_assignments(self) -> None:
        """Verify the ordinary ``name=value`` form."""
        assert parse_outputs(["a=1", "b=two"]) == {"a": "1", "b": "two"}, (
            "single-line assignments were not read back"
        )

    def test_reads_a_delimited_multiline_value(self) -> None:
        """Verify the ``name<<DELIMITER`` form."""
        lines = ["record<<EOF", "first", "second", "EOF", "after=1"]

        assert parse_outputs(lines) == {
            "record": "first\nsecond",
            "after": "1",
        }, "the heredoc form was not read back"

    def test_keeps_a_value_containing_the_heredoc_marker(self) -> None:
        """Verify ``<<`` inside a value is not read as a heredoc opener.

        A runner treats ``name<<DELIMITER`` as an opener only when the ``<<``
        precedes any ``=``. Reading ``key=a<<b`` as an opener would record the
        wrong key and swallow every later line until one equalled ``b``, so the
        harness would report outputs no runner would produce.
        """
        lines = ["bin-dir=/opt/a<<b", "version=0.5.0", "b", "trailing=1"]

        assert parse_outputs(lines) == {
            "bin-dir": "/opt/a<<b",
            "version": "0.5.0",
            "trailing": "1",
        }, "a value containing << was mistaken for a heredoc opener"

    def test_ignores_a_line_that_is_neither_form(self) -> None:
        """Verify a stray line is skipped rather than misread."""
        assert parse_outputs(["noise", "a=1"]) == {"a": "1"}, (
            "a line with no assignment was not skipped"
        )


class TestResolve:
    """Validate the action-expression subset the harness understands."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("runner.os", "Linux"),
            ("runner.arch", "X64"),
            ("runner.temp", "/temp"),
            ("github.action_path", "/action"),
            ("inputs.version", "0.5.0"),
            ("inputs.absent", ""),
            ("steps.probe.outputs.ready", "true"),
            ("steps.probe.outputs.absent", ""),
            ("steps.absent.outputs.ready", ""),
        ],
    )
    def test_resolves_each_supported_expression(self, body: str, expected: str) -> None:
        """Verify every expression form the installer manifests use."""
        context = _context(
            runner_temp="/temp",
            step_outputs={"probe": {"ready": "true"}},
        )

        assert context.resolve(body) == expected, f"{body} resolved unexpectedly"

    def test_refuses_an_unsupported_expression(self) -> None:
        """Verify an unknown expression is a harness error, not a silent empty."""
        with pytest.raises(AssertionError, match="unsupported action expression"):
            _context().resolve("github.event.number")


class TestEvaluateCondition:
    """Validate how a step's ``if:`` decides whether it runs."""

    def test_an_absent_condition_carries_an_implicit_success(self) -> None:
        """Verify a step with no condition is skipped after a failure."""
        step: dict[str, object] = {"name": "step"}

        assert _context().evaluate_condition(step) is True, (
            "an unconditional step must run while the job is passing"
        )
        assert _context(succeeded=False).evaluate_condition(step) is False, (
            "an unconditional step must be skipped once the job has failed"
        )

    def test_an_equality_condition_reads_a_step_output(self) -> None:
        """Verify the comparison the installer manifests use."""
        step: dict[str, object] = {
            "name": "step",
            "if": "${{ steps.probe.outputs.ready == 'true' }}",
        }

        assert _context(step_outputs={"probe": {"ready": "true"}}).evaluate_condition(
            step,
        ), "a matching output must select the step"
        assert not _context(
            step_outputs={"probe": {"ready": "false"}}
        ).evaluate_condition(
            step,
        ), "a differing output must skip the step"

    def test_a_failure_guard_inverts_the_implicit_success(self) -> None:
        """Verify a ``failure()`` step runs only after something has failed."""
        step: dict[str, object] = {"name": "step", "if": "${{ failure() }}"}

        assert _context().evaluate_condition(step) is False, (
            "a failure-guarded step must not run while the job is passing"
        )
        assert _context(succeeded=False).evaluate_condition(step) is True, (
            "a failure-guarded step must run once the job has failed"
        )

    def test_a_failure_guard_still_honours_its_conjunct(self) -> None:
        """Verify both halves of ``failure() && <comparison>`` are required."""
        step: dict[str, object] = {
            "name": "step",
            "if": "${{ failure() && steps.probe.outputs.ready == 'true' }}",
        }
        outputs = {"probe": {"ready": "true"}}

        assert _context(succeeded=False, step_outputs=outputs).evaluate_condition(
            step
        ), "a failed job with a matching output must select the step"
        assert not _context(
            succeeded=False,
            step_outputs={"probe": {"ready": "false"}},
        ).evaluate_condition(step), "a differing output must skip the step"

    def test_refuses_an_unsupported_condition(self) -> None:
        """Verify an unknown condition is a harness error, not a silent skip."""
        step: dict[str, object] = {"name": "step", "if": "${{ always() }}"}

        with pytest.raises(AssertionError, match="unsupported step condition"):
            _context().evaluate_condition(step)


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    """Return a finished process with ``returncode`` and no output."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout="",
        stderr="",
    )


class TestLifecycleResult:
    """Validate which exit code a run is judged by."""

    def test_reports_zero_when_every_step_passed(self) -> None:
        """Verify a passing run reports zero."""
        result = LifecycleResult(
            steps=(StepResult(name="a", process=_completed(0)),),
        )

        assert result.returncode == 0, "a passing run must report zero"

    def test_reports_the_code_that_decided_the_run(self) -> None:
        """Verify a later reporting step does not mask the failure.

        A step guarded by ``failure()`` runs after the failure it reports and
        exits zero itself, so the last exit code is not the deciding one.
        """
        result = LifecycleResult(
            steps=(
                StepResult(name="install", process=_completed(94)),
                StepResult(name="report", process=_completed(0)),
            ),
        )

        assert result.returncode == 94, (
            "the reporting step masked the exit code that decided the run"
        )


def test_the_environment_creates_its_output_directory(tmp_path: Path) -> None:
    """Verify the output directory exists before any fragment asks for a file."""
    output_dir = tmp_path / "outputs"
    environment = FragmentEnvironment(
        base_env={},
        cwd=tmp_path,
        output_dir=output_dir,
    )

    assert output_dir.is_dir(), "the output directory was not created"
    assert environment.output_file("00-output").parent == output_dir, (
        "the output file was not placed in the output directory"
    )
