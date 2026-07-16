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

    def test_non_dict_outcome_is_skipped_not_fatal(self) -> None:
        """A non-dict outcome is skipped; later valid outcomes still count."""
        payload = {"outcomes": ["garbage", _mutant_outcome("CaughtMutant")]}
        counts, _ = summarize.parse_outcomes(payload)
        assert counts["CaughtMutant"] == 1, (
            "parsing should continue past a non-dict outcome, not stop"
        )


class TestSurvivorFrom:
    """Extraction of a surviving mutant from one scenario object."""

    _PLACEHOLDER = summarize.SurvivingMutant(file="?", line=0, name="?")

    def test_non_dict_mutant_yields_placeholder(self) -> None:
        """A scenario whose ``Mutant`` is not a dict yields the placeholder."""
        assert summarize._survivor_from({"Mutant": "nope"}) == self._PLACEHOLDER, (
            "a non-dict Mutant should fall back to the ?/0/? placeholder"
        )

    def test_absent_mutant_key_yields_placeholder(self) -> None:
        """A scenario without a ``Mutant`` key yields the placeholder."""
        assert summarize._survivor_from({}) == self._PLACEHOLDER, (
            "a missing Mutant key should fall back to the ?/0/? placeholder"
        )

    def test_empty_mutant_defaults_each_field(self) -> None:
        """An empty ``Mutant`` dict defaults file, line, and name."""
        assert summarize._survivor_from({"Mutant": {}}) == self._PLACEHOLDER, (
            "an empty Mutant should default file='?', line=0, and name='?'"
        )


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

    def test_timeout_and_unviable_counts_are_carried(self, tmp_path: Path) -> None:
        """Timeout and unviable outcomes reach the merged report counts."""
        _write_report(
            tmp_path,
            "mutation-report-root-0",
            [
                _mutant_outcome("Timeout"),
                _mutant_outcome("Unviable"),
                _mutant_outcome("Unviable"),
            ],
        )
        report = summarize.collect_reports(tmp_path)[0]
        assert report.timeout == 1, "the timeout count should reach the report"
        assert report.unviable == 2, "the unviable count should reach the report"

    def test_root_target_sorts_first(self, tmp_path: Path) -> None:
        """The root target leads even when another slug sorts before it."""
        _write_report(
            tmp_path, "mutation-report-aaa-0", [_mutant_outcome("CaughtMutant")]
        )
        _write_report(
            tmp_path, "mutation-report-root-0", [_mutant_outcome("CaughtMutant")]
        )
        reports = summarize.collect_reports(tmp_path)
        assert [report.slug for report in reports] == ["root", "aaa"], (
            "root should sort first, ahead of the alphabetically-earlier slug"
        )

    def test_diagnostics_and_foreign_dir_does_not_halt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Skipped, missing, and invalid dirs emit diagnostics without halting."""
        (tmp_path / "0-foreign").mkdir()
        (tmp_path / "mutation-report-a-0").mkdir()  # missing outcomes.json
        invalid = tmp_path / "mutation-report-b-0"
        invalid.mkdir()
        (invalid / "outcomes.json").write_text("not json", encoding="utf-8")
        _write_report(
            tmp_path, "mutation-report-root-0", [_mutant_outcome("CaughtMutant")]
        )
        reports = summarize.collect_reports(tmp_path)
        out = capsys.readouterr().out
        assert "mutation_summary_skipped_dir=0-foreign" in out, (
            "an unmatched directory should emit the skipped-dir diagnostic"
        )
        assert "mutation_summary_missing_outcomes=mutation-report-a-0" in out, (
            "a directory without outcomes.json should emit the missing diagnostic"
        )
        assert "mutation_summary_invalid_outcomes=mutation-report-b-0:" in out, (
            "invalid JSON should emit the invalid-outcomes diagnostic with the name"
        )
        # The foreign dir sorts first; scanning must continue to the root report.
        assert [report.slug for report in reports] == ["root"], (
            "a leading foreign directory must not halt the scan"
        )
        assert reports[0].caught == 1, "the trailing valid report should be collected"

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

    def test_exact_markdown_layout(self) -> None:
        """The rendered Markdown matches the contracted layout byte-for-byte."""
        report = summarize.TargetReport(
            slug="root",
            caught=2,
            missed=1,
            timeout=3,
            unviable=0,
            survivors=(
                summarize.SurvivingMutant(
                    file="src/a.rs", line=7, name="replace + with -"
                ),
            ),
        )
        expected = (
            "## Mutation testing results (root)\n"
            "\n"
            "- **Caught:** 2\n"
            "- **Missed (survived):** 1\n"
            "- **Timeout:** 3\n"
            "- **Unviable:** 0\n"
            "\n"
            "### Surviving mutants\n"
            "\n"
            "| File | Line | Mutation |\n"
            "| ---- | ---- | -------- |\n"
            "| src/a.rs | 7 | replace + with - |\n"
        )
        assert summarize.render_summary([report]) == expected, (
            "the summary Markdown should match the contracted layout exactly"
        )

    def test_no_reports_message(self) -> None:
        """An empty report set renders the exact explanatory message."""
        assert (
            summarize.render_summary([])
            == "## Mutation testing results\n\nNo reports were produced.\n"
        ), "an empty report set should render the exact explanatory message"


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
