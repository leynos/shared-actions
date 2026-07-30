#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0"]
# ///

"""Merge cargo-mutants shard reports and post the job summary.

Scans a directory of downloaded ``mutation-report-<slug>-<shard>``
artefacts, merges the ``outcomes.json`` payloads per target, and appends
a Markdown summary (outcome counts plus a table of surviving mutants) to
``GITHUB_STEP_SUMMARY``.

The ``outcomes.json`` field names were validated against cargo-mutants
27.x source: the top level holds an ``outcomes`` array whose entries carry a
``scenario`` (the string ``"Baseline"`` or an object ``{"Mutant": {...}}``)
and a ``summary`` (``CaughtMutant``, ``MissedMutant``, ``Timeout``,
``Unviable``, ...). The format is documented as unstable, so this parser
and the workflow's pinned cargo-mutants version must be updated together.

Environment Variables
---------------------
INPUT_REPORT_ROOT : str
    Directory containing one subdirectory per downloaded artefact, each
    holding an ``outcomes.json``.
GITHUB_STEP_SUMMARY : str
    Path of the job-summary file.

Usage
-----
As a workflow step, after ``actions/download-artifact``::

    - run: uv run --script workflow_scripts/mutation_summarize_cargo.py
      env:
        INPUT_REPORT_ROOT: reports
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import typing as typ
from pathlib import Path

from cyclopts import App, Parameter

if __package__:
    from .output import emit, fail
else:
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

app = App()

ARTEFACT_NAME_PATTERN = re.compile(r"^mutation-report-(?P<slug>.+)-(?P<shard>\d+)$")

_COUNTED_SUMMARIES = ("CaughtMutant", "MissedMutant", "Timeout", "Unviable")


@dataclasses.dataclass(frozen=True, slots=True)
class SurvivingMutant:
    """One mutant the test suite failed to catch.

    Attributes
    ----------
    file : str
        Source file the mutation applies to.
    line : int
        1-based line of the mutation site.
    name : str
        Human-readable mutation description.
    """

    file: str
    line: int
    name: str


@dataclasses.dataclass(frozen=True, slots=True)
class TargetReport:
    """Merged outcome counts and survivors for one mutation target.

    Attributes
    ----------
    slug : str
        Target name derived from the artefact directory.
    caught : int
        Mutants caught by the suite.
    missed : int
        Mutants that survived.
    timeout : int
        Mutants whose test run timed out.
    unviable : int
        Mutants that failed to build.
    survivors : tuple[SurvivingMutant, ...]
        The surviving mutants, in report order.
    """

    slug: str
    caught: int
    missed: int
    timeout: int
    unviable: int
    survivors: tuple[SurvivingMutant, ...]


def parse_outcomes(
    payload: dict[str, object],
) -> tuple[dict[str, int], list[SurvivingMutant]]:
    """Count mutant outcomes and collect survivors from one report.

    Parameters
    ----------
    payload : dict
        Parsed ``outcomes.json`` object.

    Returns
    -------
    tuple[dict[str, int], list[SurvivingMutant]]
        Counts keyed by summary name, and the surviving mutants.
    """
    counts = dict.fromkeys(_COUNTED_SUMMARIES, 0)
    survivors: list[SurvivingMutant] = []
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list):
        return counts, survivors
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        scenario = outcome.get("scenario")
        if not isinstance(scenario, dict):
            continue  # baseline entries carry the string "Baseline"
        summary = outcome.get("summary")
        if summary in counts:
            counts[summary] += 1
        if summary == "MissedMutant":
            survivors.append(_survivor_from(scenario))
    return counts, survivors


def _survivor_from(scenario: dict[str, object]) -> SurvivingMutant:
    """Extract a surviving mutant from a ``{"Mutant": {...}}`` scenario."""
    mutant = scenario.get("Mutant")
    if not isinstance(mutant, dict):
        return SurvivingMutant(file="?", line=0, name="?")
    return SurvivingMutant(
        file=str(mutant.get("file", "?")),
        line=_start_line(mutant),
        name=str(mutant.get("name", "?")),
    )


def _start_line(mutant: dict[str, object]) -> int:
    """Return the mutation's 1-based start line, or 0 when absent."""
    span = mutant.get("span")
    start = span.get("start") if isinstance(span, dict) else None
    line = start.get("line") if isinstance(start, dict) else None
    return line if isinstance(line, int) else 0


def collect_reports(report_root: Path) -> list[TargetReport]:
    """Merge every artefact directory under ``report_root`` by target.

    Parameters
    ----------
    report_root : Path
        Directory containing downloaded artefact subdirectories.

    Returns
    -------
    list[TargetReport]
        One merged report per target slug, sorted with the root target
        first, then alphabetically.
    """
    merged: dict[str, tuple[dict[str, int], list[SurvivingMutant]]] = {}
    for artefact_dir in sorted(p for p in report_root.iterdir() if p.is_dir()):
        match = ARTEFACT_NAME_PATTERN.match(artefact_dir.name)
        if match is None:
            emit("mutation_summary_skipped_dir", artefact_dir.name)
            continue
        outcomes_path = artefact_dir / "outcomes.json"
        if not outcomes_path.is_file():
            emit("mutation_summary_missing_outcomes", artefact_dir.name)
            continue
        try:
            # pragma below: encoding is a locale-independent UTF-8 codec alias.
            raw = outcomes_path.read_text(encoding="utf-8")  # pragma: no mutate
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            emit("mutation_summary_invalid_outcomes", f"{artefact_dir.name}: {error}")
            continue
        else:
            counts, survivors = parse_outcomes(payload)
        slug = match["slug"]
        totals, all_survivors = merged.setdefault(
            slug, (dict.fromkeys(_COUNTED_SUMMARIES, 0), [])
        )
        for key, value in counts.items():
            totals[key] += value
        all_survivors.extend(survivors)
    reports = [
        TargetReport(
            slug=slug,
            caught=totals["CaughtMutant"],
            missed=totals["MissedMutant"],
            timeout=totals["Timeout"],
            unviable=totals["Unviable"],
            survivors=tuple(survivors),
        )
        for slug, (totals, survivors) in merged.items()
    ]
    return sorted(reports, key=lambda report: (report.slug != "root", report.slug))


def _escape_cell(value: str) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return value.replace("|", "\\|")


def render_summary(reports: list[TargetReport]) -> str:
    """Render the merged reports as job-summary Markdown.

    Parameters
    ----------
    reports : list[TargetReport]
        Merged per-target reports, as produced by ``collect_reports``.

    Returns
    -------
    str
        Markdown with per-target outcome counts and, where mutants
        survived, a table of file, line, and mutation description. When
        ``reports`` is empty, an explanatory message is returned instead.
    """
    if not reports:
        return "## Mutation testing results\n\nNo reports were produced.\n"
    lines: list[str] = []
    for report in reports:
        lines.extend(
            (
                f"## Mutation testing results ({report.slug})",
                "",
                f"- **Caught:** {report.caught}",
                f"- **Missed (survived):** {report.missed}",
                f"- **Timeout:** {report.timeout}",
                f"- **Unviable:** {report.unviable}",
                "",
            )
        )
        if report.survivors:
            lines.extend(
                (
                    "### Surviving mutants",
                    "",
                    "| File | Line | Mutation |",
                    "| ---- | ---- | -------- |",
                )
            )
            lines.extend(
                f"| {_escape_cell(m.file)} | {m.line} | {_escape_cell(m.name)} |"
                for m in report.survivors
            )
            lines.append("")
    return "\n".join(lines)


@app.default
def main(
    *,
    report_root: typ.Annotated[
        str, Parameter(required=True, env_var="INPUT_REPORT_ROOT")
    ],
) -> None:
    """Merge shard reports and append the Markdown job summary.

    Parameters
    ----------
    report_root : str
        Directory containing downloaded artefact subdirectories.

    Raises
    ------
    SystemExit
        Exits with code 1 when the report root or ``GITHUB_STEP_SUMMARY``
        is missing.
    """
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_env:
        fail("GITHUB_STEP_SUMMARY is not set")
    root = Path(report_root)
    if not root.is_dir():
        fail(f"report root {report_root!r} is not a directory")

    reports = collect_reports(root)
    with Path(summary_env).open("a", encoding="utf-8") as handle:
        handle.write(render_summary(reports))
    emit(
        "mutation_summary_targets",
        {report.slug: report.missed for report in reports},
    )


if __name__ == "__main__":
    app()
