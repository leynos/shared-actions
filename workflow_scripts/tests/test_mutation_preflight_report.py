"""The report the run publishes: its envelope, verdict and rendered summary.

The envelope is emitted in full before any mutant exists, the JSON report is
the artefact a later stage reads, and a verdict nobody can read must not be
published as a pass. A finding rendered into the step summary stays in its own
table row.
"""

from __future__ import annotations

import json
import sys
import typing as typ

from workflow_scripts.mutationpreflight.models import (
    PhaseResult,
    Report,
    Runtimes,
)
from workflow_scripts.mutationpreflight.reporting import render_summary
from workflow_scripts.tests.mutation_preflight_support import (
    Checkout,
    Outcome,
    lock_document,
    outcome_for,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestEnvelope:
    """The run publishes its phases and verdict with no mutants in existence."""

    def test_every_phase_is_emitted_in_order(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The envelope is complete even when nothing has been mutated."""
        # Both commands name the running interpreter: it exists on every
        # platform, so this test checks the envelope rather than which tools
        # the host happens to have. Literal strings, because a Windows
        # interpreter path holds backslashes a basic string would read as
        # escapes.
        manifest = (
            'sandbox_paths = ["README.md"]\n'
            "[[require_commands]]\n"
            f"name = '{sys.executable}'\n"
            "[native]\n"
            'package = "tei-rapporteur"\n'
            "[[checks]]\n"
            'name = "collect"\n'
            f"command = ['{sys.executable}', '-c', 'print(1)']\n"
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=manifest,
                also_copy=("conftest.py", "README.md"),
                extra={
                    "README.md": "# example\n",
                    "uv.lock": lock_document("0.3.1", 'registry = "x"'),
                },
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        order = [
            line.partition("=")[2]
            for line in outcome.stdout.splitlines()
            if line.startswith("mutation_preflight_phase=")
        ]
        names = [
            typ.cast("dict[str, object]", json.loads(item))["name"] for item in order
        ]
        assert names == [
            "manifest",
            "interpreter",
            "sandbox",
            "commands",
            "checks",
            "native",
        ]
        assert outcome.result == "pass"

    def test_the_report_is_written_without_mutation_results(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The JSON report is the artefact a later stage reads, so it always exists."""
        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        report = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert report["result"] == "pass"
        assert [phase["name"] for phase in report["phases"]] == [
            "manifest",
            "interpreter",
            "sandbox",
            "commands",
            "checks",
            "native",
        ], "a phase the declaration would have enabled is reported, not omitted"
        assert outcome.phases["manifest"]["status"] == "skipped"

    def test_an_unwritable_report_fails_the_run(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A verdict nobody can read must not be reported as a pass."""
        destination = tmp_path / "out" / "mutation-preflight.json"
        destination.mkdir(parents=True)

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 1
        assert outcome.result == "fail"
        assert not destination.is_file()

    def test_the_step_summary_is_appended_when_the_job_asks(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The Markdown summary rides on GITHUB_STEP_SUMMARY like the other gates."""
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        rendered = summary.read_text(encoding="utf-8")
        assert "## Mutation environment preflight" in rendered
        assert "| interpreter | pass |" in rendered

    def test_a_step_summary_that_cannot_be_written_fails_the_verdict(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A report whose summary failed must not be published as a pass."""
        summary = tmp_path / "summary.md"
        summary.mkdir()
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert outcome.result == "fail"
        written = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        assert written["result"] == "fail", (
            "the JSON verdict must agree with the exit status when the summary "
            "could not be appended"
        )
        assert written["reporting"], (
            "the report must record why its summary was not published"
        )

    def test_an_unknown_lane_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A lane this helper cannot check fails instead of borrowing another's."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(),
            lane="stryker",
            project_python="3.13",
        )

        assert outcome.exit_code == 2
        assert outcome.result == "fail"


class TestSummaryRow:
    """A finding cannot escape the table row it is rendered in."""

    def test_a_multiline_finding_stays_in_one_row(self) -> None:
        """ConfigParser's multi-line message must not end the row early."""
        finding = "setup.cfg could not be read: File contains no section\nheaders"
        report = Report(
            lane="mutmut",
            runtimes=Runtimes(helper="3.14.7", project="3.13"),
            results=(PhaseResult("sandbox", "unknown", (finding,)),),
            verdict="pass",
        )

        rendered = render_summary(report)

        rows = [line for line in rendered.splitlines() if line.startswith("| sandbox")]
        assert rows == [
            (
                "| sandbox | unknown | setup.cfg could not be read: File contains "
                "no section headers |"
            )
        ]

    def test_a_backslash_cannot_neutralize_the_pipe_escape(self) -> None:
        """Escaping the pipe after a backslash would leave the pipe unescaped."""
        report = Report(
            lane="mutmut",
            runtimes=Runtimes(helper="3.14.7", project="3.13"),
            results=(PhaseResult("sandbox", "fail", ("a\\|b",)),),
            verdict="fail",
        )

        rendered = render_summary(report)

        assert "a\\\\\\|b" in rendered
