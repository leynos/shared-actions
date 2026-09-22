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
                f"uv run --script {lane.PROOF_SCRIPT} --runner R", True, id="uv-script"
            ),
            pytest.param(f"uv run {lane.PROOF_SCRIPT} --runner R", True, id="uv-run"),
            pytest.param(f"{lane.PROOF_SCRIPT} --runner R", True, id="direct"),
            pytest.param(f"./{lane.PROOF_SCRIPT} --runner R", True, id="direct-dotted"),
            pytest.param(
                f"if uv run --script {lane.PROOF_SCRIPT} --runner R; then :; fi",
                True,
                id="behind-if",
            ),
            pytest.param(
                f"RUST_LOG=debug uv run --script {lane.PROOF_SCRIPT} --runner R",
                True,
                id="behind-an-assignment",
            ),
            pytest.param(f"true {lane.PROOF_SCRIPT} --runner R", False, id="true"),
            pytest.param(f": {lane.PROOF_SCRIPT} --runner R", False, id="colon"),
            pytest.param(f"echo {lane.PROOF_SCRIPT} --runner R", False, id="echo"),
            pytest.param(
                f"uv run --script other.py {lane.PROOF_SCRIPT} --runner R",
                False,
                id="an-argument-to-another-script",
            ),
            pytest.param(f"# {lane.PROOF_SCRIPT} --runner R", False, id="comment"),
        ],
    )
    def test_only_a_launcher_in_the_command_position_counts(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """The script runs only when it or `uv run` holds the command position.

        Anything else that names the path, `true` above all, exits zero
        and proves nothing, so it must not satisfy the lane rule.
        """
        arguments = [
            found
            for words in lane._command_lines(script)
            if (found := lane._proof_arguments(words)) is not None
        ]

        assert (arguments == [["--runner", "R"]]) is expected, (
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
