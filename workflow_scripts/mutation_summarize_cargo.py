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
            payload = json.loads(outcomes_path.read_text(encoding="utf-8"))
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


#: Where each shard's artefact carries the mutants it enumerated.
MUTANT_INVENTORY = "mutants.json"

#: Reported when no shard enumerated anything.
NO_MUTANTS = "no mutants found"


def _shard_inventory_count(artefact_dir: Path) -> int | None:
    """Count one shard's mutants, or None when its inventory is unusable."""
    try:
        listed = json.loads(
            (artefact_dir / MUTANT_INVENTORY).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return len(listed) if isinstance(listed, list) else None


def total_enumerated_mutants(report_root: Path) -> int | None:
    """Sum the mutants every shard enumerated.

    A single empty shard is ordinary: sharding splits the inventory after
    enumeration, so a project with fewer mutants than shards leaves some
    shards with nothing. A run where *every* shard is empty is the
    vacuous pass, and this job is the only place that can see it, because
    it is the only one holding all the shards at once.

    Parameters
    ----------
    report_root : Path
        Directory containing the downloaded artefact subdirectories.

    Returns
    -------
    int | None
        The total, or ``None`` when any shard's inventory is missing or
        unreadable. Unknown is deliberately not zero, and one unknown
        shard makes the total unknown rather than merely smaller.
    """
    shards = tuple(
        sorted(
            entry
            for entry in report_root.iterdir()
            if entry.is_dir() and ARTEFACT_NAME_PATTERN.match(entry.name)
        )
    )
    counts = tuple(_shard_inventory_count(directory) for directory in shards)
    # One unreadable shard makes the whole total unknown. Summing the rest
    # would report a run as empty on the strength of the shards that
    # happened to survive, and failing a job for that is exactly the kind
    # of confident wrong answer this check exists to prevent.
    if not counts or any(count is None for count in counts):
        return None
    return sum(count for count in counts if count is not None)


def check_the_run_was_not_empty(*, total: int | None, allowed: bool) -> bool:
    """Decide whether an all-empty run should fail the job.

    Parameters
    ----------
    total : int | None
        Mutants enumerated across every shard, or ``None`` when unknown.
    allowed : bool
        Whether the caller opted in to an empty run.

    Returns
    -------
    bool
        ``True`` when the job may pass.
    """
    if total is None:
        return _report_unknown_total()
    emit("mutation_summary_mutants", total)
    return True if total > 0 else _report_empty_run(allowed=allowed)


def _report_unknown_total() -> bool:
    """Announce an unreadable inventory and let the job pass."""
    print(
        "::warning title=Mutation testing::no shard carried a readable "
        "mutant inventory, so an empty run cannot be told from a full one"
    )
    emit("mutation_summary_inventory", "unreadable")
    return True


def _report_empty_run(*, allowed: bool) -> bool:
    """Announce an all-empty run and decide whether it may pass."""
    if allowed:
        print(
            "::notice title=Mutation testing::no mutants were found in any "
            "shard, which this caller has declared expected"
        )
        emit("mutation_summary_outcome", f"{NO_MUTANTS} (allowed)")
        return True
    print(
        "::error title=Mutation testing::no mutants were found in any shard, "
        "so this run proved nothing; widen the filters, or set "
        "allow-no-mutants: true if an empty run is expected here"
    )
    emit("mutation_summary_outcome", NO_MUTANTS)
    return False


@app.default
def main(
    *,
    report_root: typ.Annotated[
        str, Parameter(required=True, env_var="INPUT_REPORT_ROOT")
    ],
    allow_no_mutants: typ.Annotated[
        str, Parameter(env_var="INPUT_ALLOW_NO_MUTANTS")
    ] = "false",
) -> None:
    """Merge shard reports and append the Markdown job summary.

    Parameters
    ----------
    report_root : str
        Directory containing downloaded artefact subdirectories.
    allow_no_mutants : str
        ``true`` to accept a run in which no shard found a mutant.

    Raises
    ------
    SystemExit
        Exits with code 1 when the report root or ``GITHUB_STEP_SUMMARY``
        is missing, and when no shard enumerated a mutant and the caller
        did not opt in to that.
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
    # After the summary is written, deliberately: an operator reading a
    # failed job still gets the table that says what did run.
    if not check_the_run_was_not_empty(
        total=total_enumerated_mutants(root),
        allowed=allow_no_mutants.strip().lower() == "true",
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    app()
