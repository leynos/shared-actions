"""Tests for the readers behind the coverage watchdog lane contract.

`test_coverage_watchdog_lane.py` holds the rules. This module holds what
those rules depend on being true of their readers: which job shapes are
refused, which run blocks count as executing the proof, and what the
read boundary refuses. Kept apart so the rule module reads as the
contract it states, and each half stays small enough to review.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import _watchdog_command_reading as shell
from . import test_coverage_watchdog_lane as lane

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestTheWorkflowReaders:
    """What the readers refuse, so that the narrowing is not decoration.

    Every assertion above reads its fields through these helpers. If the
    helpers accepted whatever the parser handed back, the annotations
    would describe an intention rather than a fact, and a workflow that
    had lost its shape would reach an assertion as something unchecked.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("a string", "not a mapping", id="not-a-mapping"),
            pytest.param(
                {"runs-on": ["ubuntu-latest"]},
                "runs-on that is not a string",
                id="runs-on-as-a-list",
            ),
            pytest.param(
                {"steps": [["not", "a", "mapping"]]},
                "step 0 is not a mapping",
                id="a-step-that-is-not-a-mapping",
            ),
        ],
    )
    def test_a_job_that_is_not_the_expected_shape_is_refused(
        self, value: object, expected: str
    ) -> None:
        """A job the contract cannot read fails here, not at an assertion.

        The list form of `runs-on` is the one to watch. GitHub accepts it,
        this lane does not use it, and reading it as a string downstream
        would compare the fork expression against a repr and report a
        confusing mismatch instead of the real problem.
        """
        with pytest.raises(TypeError, match=expected):
            lane._as_job(value, where="job under test")

    def test_a_job_without_steps_reads_as_having_none(self) -> None:
        """A job declaring no steps is a job, not an error.

        It is a job this contract has something to say about: it runs the
        proof nowhere.
        """
        assert lane._as_job({"runs-on": "ubicloud-standard-2"}, where="job") == {
            "runs-on": "ubicloud-standard-2",
            "steps": [],
        }


class TestTheProofCommand:
    """Which run blocks count as executing the proof script."""

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            pytest.param(
                f"uv run --script {shell.PROOF_SCRIPT} --runner R",
                True,
                id="uv-script",
            ),
            pytest.param(f"uv run {shell.PROOF_SCRIPT} --runner R", True, id="uv-run"),
            pytest.param(f"{shell.PROOF_SCRIPT} --runner R", True, id="direct"),
            pytest.param(
                f"./{shell.PROOF_SCRIPT} --runner R", True, id="direct-dotted"
            ),
            pytest.param(
                f"time uv run --script {shell.PROOF_SCRIPT} --runner R",
                True,
                id="behind-time",
            ),
            pytest.param(
                f"if false; then uv run --script {shell.PROOF_SCRIPT} --runner R; fi",
                False,
                id="behind-if-false",
            ),
            pytest.param(
                f"false && uv run --script {shell.PROOF_SCRIPT} --runner R",
                False,
                id="after-and",
            ),
            pytest.param(
                f"true || uv run --script {shell.PROOF_SCRIPT} --runner R",
                False,
                id="after-or",
            ),
            pytest.param(
                f"uv run --script {shell.PROOF_SCRIPT} --runner R | tee log",
                False,
                id="into-a-pipe",
            ),
            pytest.param(
                f"! uv run --script {shell.PROOF_SCRIPT} --runner R",
                False,
                id="negated",
            ),
            pytest.param(
                f"RUST_LOG=debug uv run --script {shell.PROOF_SCRIPT} --runner R",
                True,
                id="behind-an-assignment",
            ),
            pytest.param(f"true {shell.PROOF_SCRIPT} --runner R", False, id="true"),
            pytest.param(f": {shell.PROOF_SCRIPT} --runner R", False, id="colon"),
            pytest.param(f"echo {shell.PROOF_SCRIPT} --runner R", False, id="echo"),
            pytest.param(
                f"uv run --script other.py {shell.PROOF_SCRIPT} --runner R",
                False,
                id="an-argument-to-another-script",
            ),
            pytest.param(f"# {shell.PROOF_SCRIPT} --runner R", False, id="comment"),
        ],
    )
    def test_only_a_launcher_in_the_command_position_counts(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """The script runs only when it or `uv run` holds the command position.

        Anything else that names the path, `true` above all, exits zero
        and proves nothing, so it must not satisfy the lane rule. Nor may
        an invocation that runs only on some condition, or whose failure
        the step would not see: behind `if`, after `&&` or `||`, into a
        pipe, or negated.
        """
        arguments = [
            found
            for words in shell.command_lines(script)
            if (found := shell.proof_arguments(words)) is not None
        ]

        # A refused case must yield no invocation at all, not merely a
        # different one: the lane rule accepts any invocation carrying the
        # runner, so `<proof> --runner R | tee log` read with extra
        # arguments would still satisfy it.
        assert arguments == ([["--runner", "R"]] if expected else []), (
            f"{script!r} should {'' if expected else 'not '}count as executing "
            f"the proof; read as {arguments!r}"
        )


class TestTheReadBoundary:
    """What `_read` refuses, so that every reader after it is pure."""

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            pytest.param("jobs: [unclosed\n", ValueError, id="invalid-yaml"),
            pytest.param("- a list\n", TypeError, id="not-a-mapping"),
            pytest.param(
                "jobs:\n  a:\n    runs-on: ubuntu-latest\n    runs-on: x\n",
                ValueError,
                id="duplicate-key",
            ),
        ],
    )
    def test_an_unreadable_workflow_fails_naming_the_file(
        self, tmp_path: Path, text: str, error: type[Exception]
    ) -> None:
        """A parse failure and a shape failure both name the file."""
        path = tmp_path / "broken.yml"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(error, match=re.escape(str(path))):
            lane._read(path)

    def test_a_missing_workflow_fails_naming_the_file(self, tmp_path: Path) -> None:
        """A file that is not there is a read failure, reported as one."""
        path = tmp_path / "absent.yml"

        with pytest.raises(ValueError, match=re.escape(str(path))):
            lane._read(path)

    def test_the_readers_answer_for_the_workflow_given(self, tmp_path: Path) -> None:
        """Jobs, triggers and the raw runner come from the file passed in.

        A reader that still reached for the module's fixed path would
        report the real lane here, not this one.
        """
        path = tmp_path / "lane.yml"
        path.write_text(
            "on:\n"
            "  pull_request:\n"
            "    types: [closed]\n"
            "jobs:\n"
            "  probe:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
            encoding="utf-8",
        )
        workflow = lane._read(path)

        assert sorted(lane._jobs(workflow)) == ["probe"]
        assert lane._triggers(workflow) == {"pull_request": {"types": ["closed"]}}
        assert lane._raw_runs_on(workflow, "probe").strip() == "ubuntu-latest"


#: The proof invocation the here-document cases place in and around a body.
_PROOF_LINE: typ.Final[str] = f"uv run --script {shell.PROOF_SCRIPT} --runner R"


class TestHereDocuments:
    """A here-document body is input to a command, never a command."""

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            pytest.param(
                f"cat <<'EOF'\n{_PROOF_LINE}\nEOF\n",
                False,
                id="inside-a-quoted-body",
            ),
            pytest.param(
                f"cat <<EOF\n{_PROOF_LINE}\nEOF\n",
                False,
                id="inside-an-unquoted-body",
            ),
            pytest.param(
                f"cat <<-EOF\n\t{_PROOF_LINE}\n\tEOF\n",
                False,
                id="inside-a-tab-stripped-body",
            ),
            pytest.param(
                f"cat <<'EOF'\nnotes\nEOF\n{_PROOF_LINE}\n",
                True,
                id="after-the-body-closes",
            ),
            pytest.param(
                f"cat <<< x\n{_PROOF_LINE}\n",
                True,
                id="after-a-here-string",
            ),
        ],
    )
    def test_only_commands_outside_a_body_count(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """A body's lines run nothing; the lines after its delimiter do.

        A here-string has no body, so the line after it is a command.
        """
        arguments = [
            found
            for words in shell.command_lines(script)
            if (found := shell.proof_arguments(words)) is not None
        ]

        assert arguments == ([["--runner", "R"]] if expected else []), (
            f"{script!r} should {'' if expected else 'not '}count as executing "
            f"the proof; read as {arguments!r}"
        )


class TestGuards:
    """Which steps and jobs count as always running and always failing."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param({"run": "x"}, True, id="plain"),
            pytest.param(
                {"run": "x", "continue-on-error": False}, True, id="coe-false"
            ),
            pytest.param({"run": "x", "if": "false"}, False, id="if-false"),
            pytest.param({"run": "x", "if": "always()"}, False, id="any-if"),
            pytest.param({"run": "x", "continue-on-error": True}, False, id="coe-true"),
            pytest.param(
                {"run": "x", "continue-on-error": "${{ true }}"},
                False,
                id="coe-expression",
            ),
        ],
    )
    def test_a_guarded_step_is_not_the_proof(
        self,
        value: dict[str, object],
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Any `if`, and any `continue-on-error` but false, disqualifies a step.

        An `if` may switch the proof off; a `continue-on-error` lets it
        fail without failing the lane. Neither is read for its value
        beyond a literal false, because an expression can be anything.
        """
        step = lane._as_step(value, where="step")

        assert lane._is_unguarded(step) is expected, (
            f"{value!r} should {'' if expected else 'not '}count as unguarded"
        )

    def test_a_guarded_job_is_read_as_guarded(self) -> None:
        """A job's own `if` and `continue-on-error` survive the narrowing."""
        job = lane._as_job(
            {"if": "false", "continue-on-error": True, "steps": []}, where="job"
        )

        assert not lane._is_unguarded(job), job
