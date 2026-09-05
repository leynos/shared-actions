"""Unit tests for the cargo-mutants summary merge script."""

from __future__ import annotations

import json
import typing as typ

import pytest

from workflow_scripts import mutation_summarize_cargo as summarize

if typ.TYPE_CHECKING:
    from pathlib import Path


def _mutant_outcome(
    summary: str, *, file: str = "src/lib.rs", line: int = 7, name: str = "replace x"
) -> dict[str, object]:
    """Build one non-baseline outcome entry."""
    return {
        "scenario": {
            "Mutant": {
                "file": file,
                "name": name,
                "span": {"start": {"line": line, "column": 1}},
            }
        },
        "summary": summary,
    }


def _write_report(root: Path, artefact: str, outcomes: list[dict[str, object]]) -> None:
    """Write an ``outcomes.json`` under one artefact directory."""
    directory = root / artefact
    directory.mkdir(parents=True)
    payload = {"outcomes": [{"scenario": "Baseline", "summary": "Success"}, *outcomes]}
    (directory / "outcomes.json").write_text(json.dumps(payload), encoding="utf-8")


class TestParseOutcomes:
    """Parsing of a single outcomes.json payload."""

    def test_counts_and_survivors(self) -> None:
        """Counts group by summary; survivors carry file, line, and name."""
        payload = {
            "outcomes": [
                {"scenario": "Baseline", "summary": "Success"},
                _mutant_outcome("CaughtMutant"),
                _mutant_outcome("MissedMutant", file="src/a.rs", line=42, name="m1"),
                _mutant_outcome("Timeout"),
                _mutant_outcome("Unviable"),
            ]
        }
        counts, survivors = summarize.parse_outcomes(payload)
        assert counts == {
            "CaughtMutant": 1,
            "MissedMutant": 1,
            "Timeout": 1,
            "Unviable": 1,
        }, "counts should group non-baseline outcomes by summary"
        assert survivors == [
            summarize.SurvivingMutant(file="src/a.rs", line=42, name="m1")
        ], "survivors should carry the missed mutant's file, line, and name"

    def test_empty_payload_is_harmless(self) -> None:
        """An empty report yields zero counts and no survivors."""
        counts, survivors = summarize.parse_outcomes({})
        assert sum(counts.values()) == 0, "an empty payload should yield zero counts"
        assert survivors == [], "an empty payload should yield no survivors"


class TestCollectReports:
    """Merging of shard artefact directories."""

    def test_shards_merge_per_target(self, tmp_path: Path) -> None:
        """Shards of one target sum their counts and pool survivors."""
        _write_report(
            tmp_path,
            "mutation-report-root-0",
            [
                _mutant_outcome("CaughtMutant"),
                _mutant_outcome("MissedMutant", name="a"),
            ],
        )
        _write_report(
            tmp_path,
            "mutation-report-root-1",
            [_mutant_outcome("MissedMutant", name="b")],
        )
        _write_report(
            tmp_path,
            "mutation-report-testkit-0",
            [_mutant_outcome("CaughtMutant")],
        )
        reports = summarize.collect_reports(tmp_path)
        assert [report.slug for report in reports] == ["root", "testkit"], (
            "reports should merge per target slug in sorted order"
        )
        root = reports[0]
        assert root.caught == 1, "caught counts should sum across a target's shards"
        assert root.missed == 2, "missed counts should sum across a target's shards"
        assert {survivor.name for survivor in root.survivors} == {"a", "b"}, (
            "survivors should pool across a target's shards"
        )

    def test_malformed_and_foreign_dirs_are_skipped(self, tmp_path: Path) -> None:
        """Unrelated directories and invalid JSON do not break the merge."""
        (tmp_path / "unrelated").mkdir()
        broken = tmp_path / "mutation-report-root-0"
        broken.mkdir()
        (broken / "outcomes.json").write_text("not json", encoding="utf-8")
        empty = tmp_path / "mutation-report-root-1"
        empty.mkdir()
        _write_report(tmp_path, "mutation-report-root-2", [])
        reports = summarize.collect_reports(tmp_path)
        assert len(reports) == 1, (
            "unrelated directories and invalid JSON should be skipped"
        )
        assert reports[0].missed == 0, (
            "the surviving report should count no missed mutants"
        )


class TestRenderSummary:
    """Markdown rendering of merged reports."""

    def test_survivor_table_and_counts(self, tmp_path: Path) -> None:
        """The summary lists counts and a survivors table per target."""
        _write_report(
            tmp_path,
            "mutation-report-root-0",
            [_mutant_outcome("MissedMutant", file="src/a|b.rs", name="swap | ops")],
        )
        rendered = summarize.render_summary(summarize.collect_reports(tmp_path))
        assert "## Mutation testing results (root)" in rendered, (
            "the summary should carry a heading per target"
        )
        assert "- **Missed (survived):** 1" in rendered, (
            "the summary should list the missed count"
        )
        assert "| src/a\\|b.rs | 7 | swap \\| ops |" in rendered, (
            "the survivor table should escape pipe characters"
        )

    def test_no_reports_message(self) -> None:
        """An empty report set renders an explanatory message."""
        assert "No reports were produced" in summarize.render_summary([]), (
            "an empty report set should render an explanatory message"
        )


class TestMainEntry:
    """End-to-end behaviour of the CLI entry point."""

    def test_appends_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job summary receives the rendered Markdown."""
        reports = tmp_path / "reports"
        reports.mkdir()
        _write_report(
            reports, "mutation-report-root-0", [_mutant_outcome("MissedMutant")]
        )
        summary_file = tmp_path / "summary.md"
        summary_file.touch()
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        monkeypatch.setenv("INPUT_REPORT_ROOT", str(reports))
        summarize.app([])
        text = summary_file.read_text(encoding="utf-8")
        assert "Surviving mutants" in text, (
            "the job summary should receive the rendered Markdown"
        )

    def test_missing_report_root_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing report root is a hard error."""
        summary_file = tmp_path / "summary.md"
        summary_file.touch()
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        monkeypatch.setenv("INPUT_REPORT_ROOT", str(tmp_path / "absent"))
        with pytest.raises(SystemExit) as excinfo:
            summarize.app([])
        assert excinfo.value.code == 1, "a missing report root should exit with code 1"


def _write_inventory(root: Path, name: str, count: int | None) -> None:
    """Write one shard artefact directory carrying a mutant inventory."""
    artefact = root / name
    artefact.mkdir(parents=True, exist_ok=True)
    if count is not None:
        entries = [{"function": f"f{index}"} for index in range(count)]
        (artefact / "mutants.json").write_text(json.dumps(entries), encoding="utf-8")


class TestTotalEnumeratedMutants:
    """Summing what every shard enumerated."""

    def test_it_sums_across_shards(self, tmp_path: Path) -> None:
        """The whole point: one shard's emptiness is not the run's.

        Every shard carries mutants and each count differs, so dropping
        any one of them changes the total. A fixture with a single
        populated shard would let a summer that skipped a shard pass.
        """
        _write_inventory(tmp_path, "mutation-report-root-0", 2)
        _write_inventory(tmp_path, "mutation-report-root-1", 3)
        _write_inventory(tmp_path, "mutation-report-root-2", 4)

        assert summarize.total_enumerated_mutants(tmp_path) == 9, (
            "the total must be the sum of every shard's inventory"
        )

    def test_an_empty_shard_does_not_hide_a_populated_one(self, tmp_path: Path) -> None:
        """One shard's emptiness is not the run's."""
        _write_inventory(tmp_path, "mutation-report-root-0", 0)
        _write_inventory(tmp_path, "mutation-report-root-1", 3)

        assert summarize.total_enumerated_mutants(tmp_path) == 3, (
            "an empty shard beside a populated one totals the populated one"
        )

    def test_all_empty_shards_total_zero(self, tmp_path: Path) -> None:
        """The vacuous run this check exists to catch."""
        _write_inventory(tmp_path, "mutation-report-root-0", 0)
        _write_inventory(tmp_path, "mutation-report-root-1", 0)

        assert summarize.total_enumerated_mutants(tmp_path) == 0, (
            "every shard empty must total zero, not unknown"
        )

    def test_no_readable_inventory_is_unknown(self, tmp_path: Path) -> None:
        """Unknown is deliberately not zero."""
        _write_inventory(tmp_path, "mutation-report-root-0", None)

        assert summarize.total_enumerated_mutants(tmp_path) is None, (
            "a run with no readable inventory must not read as empty"
        )

    def test_one_unreadable_shard_makes_the_total_unknown(self, tmp_path: Path) -> None:
        """Partial evidence must not be reported as a complete answer.

        An empty shard beside an unreadable one would otherwise sum to
        zero and fail the job as an empty run, on the strength of the
        shards that happened to survive.
        """
        _write_inventory(tmp_path, "mutation-report-root-0", 0)
        _write_inventory(tmp_path, "mutation-report-root-1", None)

        assert summarize.total_enumerated_mutants(tmp_path) is None, (
            "one unreadable shard must make the total unknown, not zero"
        )

    def test_a_foreign_directory_does_not_make_the_total_unknown(
        self, tmp_path: Path
    ) -> None:
        """Only shard artefacts count.

        The download root can hold directories that were never shard
        artefacts. Treating one as an unreadable shard would make every
        total unknown and silently disable the check.
        """
        _write_inventory(tmp_path, "mutation-report-root-0", 5)
        (tmp_path / "unrelated").mkdir()

        assert summarize.total_enumerated_mutants(tmp_path) == 5, (
            "a directory that is not a shard artefact must be ignored"
        )


class TestCheckTheRunWasNotEmpty:
    """The aggregate verdict."""

    def test_a_populated_run_passes(self, capsys: pytest.CaptureFixture) -> None:
        """The ordinary case stays quiet."""
        assert summarize.check_the_run_was_not_empty(total=7, allowed=False), (
            "a run with mutants must pass"
        )
        assert "::error" not in capsys.readouterr().out, "no annotation is warranted"

    def test_an_all_empty_run_fails(self, capsys: pytest.CaptureFixture) -> None:
        """Zero across every shard is the vacuity being fixed."""
        assert not summarize.check_the_run_was_not_empty(total=0, allowed=False), (
            "an all-empty run must fail the job"
        )

        output = capsys.readouterr().out
        assert "::error title=Mutation testing::" in output, output
        assert f"mutation_summary_outcome={summarize.NO_MUTANTS}" in output, output

    def test_an_all_empty_run_the_caller_expects_passes(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Opting out changes the verdict, never the report."""
        assert summarize.check_the_run_was_not_empty(total=0, allowed=True), (
            "the opt-out must let the job pass"
        )

        output = capsys.readouterr().out
        assert "::notice title=Mutation testing::" in output, output
        assert "(allowed)" in output, output

    def test_an_unknown_total_passes_but_says_so(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Unknown must not fail every consumer, nor pass silently."""
        assert summarize.check_the_run_was_not_empty(total=None, allowed=False), (
            "unknown must not fail the job"
        )

        output = capsys.readouterr().out
        assert "::warning title=Mutation testing::" in output, output
        assert "mutation_summary_inventory=unreadable" in output, output
