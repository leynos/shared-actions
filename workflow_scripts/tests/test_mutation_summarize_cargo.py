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
        }
        assert survivors == [
            summarize.SurvivingMutant(file="src/a.rs", line=42, name="m1")
        ]

    def test_empty_payload_is_harmless(self) -> None:
        """An empty report yields zero counts and no survivors."""
        counts, survivors = summarize.parse_outcomes({})
        assert sum(counts.values()) == 0
        assert survivors == []


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
        assert [report.slug for report in reports] == ["root", "testkit"]
        root = reports[0]
        assert root.caught == 1
        assert root.missed == 2
        assert {survivor.name for survivor in root.survivors} == {"a", "b"}

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
        assert len(reports) == 1
        assert reports[0].missed == 0


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
        assert "## Mutation testing results (root)" in rendered
        assert "- **Missed (survived):** 1" in rendered
        assert "| src/a\\|b.rs | 7 | swap \\| ops |" in rendered

    def test_no_reports_message(self) -> None:
        """An empty report set renders an explanatory message."""
        assert "No reports were produced" in summarize.render_summary([])


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
        assert "Surviving mutants" in text

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
        assert excinfo.value.code == 1
