"""Publishing what a run decided: the summary, the report and its envelope.

The job summary is Markdown a human reads, the JSON report is the artefact a
later stage reads, and each phase's envelope is the same shape in both. They
are one module because they are one publication: a report that claimed a pass
while its summary had failed would be misleading, so the two are written
together and their problems returned together.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from .constants import FAILED

if typ.TYPE_CHECKING:
    from .models import PhaseResult, Report
    from .ports import EvidenceWriter


def phase_envelope(result: PhaseResult) -> dict[str, object]:
    """Return one phase's machine-readable envelope entry.

    Parameters
    ----------
    result : PhaseResult
        The phase to publish.

    Returns
    -------
    dict[str, object]
        The phase's name, status, findings and evidence. Every value is
        JSON-serializable, because the same entry is logged and written to the
        report.

    """
    return {
        "name": result.name,
        "status": result.status,
        "findings": list(result.findings),
        "detail": result.detail,
    }


def _table_ready(findings: typ.Iterable[str]) -> list[str]:
    """Return findings that cannot break the row they are rendered in.

    A finding can carry a multi-line message, such as the one ``ConfigParser``
    raises, and an embedded newline would end the table row early. Whitespace
    runs collapse to one space. A backslash is escaped before ``|`` so a
    backslash already in the text cannot neutralize the pipe escape.
    """
    return [
        re.sub(r"\s+", " ", finding).replace("\\", "\\\\").replace("|", "\\|")
        for finding in findings
    ]


def render_summary(report: Report) -> str:
    """Render every phase as job-summary Markdown.

    Parameters
    ----------
    report : Report
        The run to render.

    Returns
    -------
    str
        The Markdown appended to the job summary.

    """
    lines = [
        "## Mutation environment preflight",
        "",
        f"- **lane:** {report.lane}",
        f"- **helper interpreter:** {report.runtimes.helper}",
        f"- **project interpreter:** {report.runtimes.project or '(not supplied)'}",
        "",
        "| Phase | Result | Findings |",
        "| ----- | ------ | -------- |",
    ]
    for result in report.results:
        findings = "<br>".join(_table_ready(result.findings)) or "-"
        lines.append(f"| {result.name} | {result.status} | {findings} |")
    lines.append("")
    return "\n".join(lines)


def _report_document(
    report: Report, unpublished: typ.Sequence[str]
) -> dict[str, object]:
    """Return the machine-readable report document.

    *unpublished* names the parts of the report that could not be published
    elsewhere. The document records them, and its verdict fails, because a
    report whose evidence is incomplete must not read as a pass.
    """
    return {
        "lane": report.lane,
        "helper_python": report.runtimes.helper,
        "project_python": report.runtimes.project,
        "result": FAILED if unpublished else report.verdict,
        "reporting": list(unpublished),
        "phases": [phase_envelope(result) for result in report.results],
    }


def write_report(
    writer: EvidenceWriter,
    report: Report,
    *,
    output_dir: Path,
    summary_path: str = "",
) -> tuple[str, ...]:
    """Append the Markdown summary, then write the JSON report.

    The summary is appended first so the JSON report can record whether it was
    published. The JSON is the machine-readable verdict, and a verdict that
    claimed a pass while its summary had failed would be misleading.

    Parameters
    ----------
    writer : EvidenceWriter
        How the summary and the report are written.
    report : Report
        The run to publish.
    output_dir : Path
        Where the JSON report is written.
    summary_path : str
        The job summary the run appends to, or ``""`` when the caller sets
        none.

    Returns
    -------
    tuple[str, ...]
        Why the diagnostics could not be published, or an empty tuple when
        they were. A run whose evidence cannot be read has not passed, so the
        caller fails it rather than exiting successfully with nothing to show.

    """
    problems: list[str] = []
    if summary_path:
        problem = writer.append_summary(Path(summary_path), render_summary(report))
        if problem is not None:
            problems.append(problem)
    written = writer.write_report(output_dir, _report_document(report, problems))
    if written is not None:
        problems.append(written)
    return tuple(problems)
