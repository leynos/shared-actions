"""Exercise the composite-action fragment harness itself.

The harness stands between a test and the action it exercises, so a defect here
makes a suite report an outcome no runner would produce. These tests pin the
parts that decide what a fragment sees and what it is recorded as producing.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    LifecycleResult,
    StepResult,
    parse_outputs,
    run_step,
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


def test_asking_for_an_output_file_changes_nothing(tmp_path: Path) -> None:
    """Verify the path query has no effect on the filesystem."""
    output_dir = tmp_path / "outputs"
    environment = FragmentEnvironment(base_env={}, cwd=tmp_path, output_dir=output_dir)
    before = sorted(path.name for path in output_dir.iterdir())

    requested = environment.output_file("00-output")

    assert not requested.exists(), "the query created the file it named"
    assert sorted(path.name for path in output_dir.iterdir()) == before, (
        "the query changed the output directory's contents"
    )


def _run_fragment(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` through the harness and return its process result."""
    environment = FragmentEnvironment(
        base_env=dict(os.environ),
        cwd=tmp_path,
        output_dir=tmp_path / "outputs",
    )
    step: dict[str, object] = {"name": "fragment", "shell": "bash", "run": script}
    return run_step(step, _context(), environment, "00-output")


def test_a_fragment_runs_from_a_file(tmp_path: Path) -> None:
    """Verify the harness runs a fragment the way a runner runs it.

    A runner writes a composite ``run:`` fragment to a file and executes that
    file. Passing the same text to ``bash -c`` is a different invocation, and
    the difference is observable: see the sibling test below.
    """
    process = _run_fragment(tmp_path, 'printf "%s\n" "$0"')

    assert process.returncode == 0, process.stderr
    assert process.stdout.strip().endswith(".sh"), (
        f"the fragment was not run from a file; $0 was {process.stdout.strip()!r}"
    )


def test_an_err_trap_in_a_fragment_still_fires(tmp_path: Path) -> None:
    """Verify a fragment's ``ERR`` trap runs when its last command fails.

    This is a regression guard for a harness defect, not for a shell feature.
    Bash 3.2, which macOS runners ship, replaces itself with the last command
    of a ``bash -c`` string when that command is external, which discards the
    trap along with the shell. Bash 5 suppresses that optimisation when a trap
    is set, so the harness ran green on Linux and reported a failure with no
    annotation and no metric on macOS. Running the fragment from a file, as a
    runner does, is not subject to it.
    """
    process = _run_fragment(
        tmp_path,
        "set -Eeuo pipefail\ntrap 'echo trap-fired' ERR\n/usr/bin/false\n",
    )

    assert process.returncode != 0, "the failing fragment reported success"
    assert "trap-fired" in process.stdout, (
        f"the fragment's ERR trap did not run; stdout was {process.stdout!r}"
    )
