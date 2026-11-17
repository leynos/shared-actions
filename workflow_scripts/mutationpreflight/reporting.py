"""Running the phases and publishing what each one decided.

Every phase is emitted as it finishes, so a crash in a later phase still leaves
the earlier verdicts in the log. That is the point of an envelope that does not
depend on mutation results existing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import typing as typ
from pathlib import Path

from .checks import run_checks_phase
from .commands import run_commands_phase
from .console import emit
from .constants import FAILED, SKIPPED, SUCCEEDED
from .interpreters import run_interpreter_phase
from .models import Declaration, Loaded, PhaseResult, Report, Runtimes
from .native import run_native_phase
from .sandbox import run_sandbox_phase

_NO_DECLARATION: typ.Final[str] = (
    "the repository declares no mutation environment requirements, so "
    "only the interpreter was checked"
)


def manifest_phase(loaded: Loaded) -> PhaseResult:
    """Return the manifest phase for what reading the declaration produced.

    Parameters
    ----------
    loaded : Loaded
        What reading the declaration produced: its source, the parsed
        declaration when one was accepted, and why it was refused otherwise.

    Returns
    -------
    PhaseResult
        ``skipped`` when the repository declares nothing, ``fail`` when the
        declaration was refused, ``pass`` otherwise.

    """
    if loaded.source is None:
        return PhaseResult("manifest", SKIPPED, (), {"reason": _NO_DECLARATION})
    detail: dict[str, object] = {"source": str(loaded.source)}
    if loaded.findings:
        return PhaseResult("manifest", FAILED, loaded.findings, detail)
    return PhaseResult("manifest", SUCCEEDED, (), detail)


def verdict(results: typ.Sequence[PhaseResult]) -> str:
    """Return the run's verdict: any failure fails the run.

    Parameters
    ----------
    results : Sequence[PhaseResult]
        Every phase that ran, in order.

    Returns
    -------
    str
        ``fail`` when any phase failed, else ``pass``.

    """
    if any(result.status == FAILED for result in results):
        return FAILED
    return SUCCEEDED


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
    backslash already in the text cannot neutralise the pipe escape.
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


def _append_summary(path: Path, rendered: str) -> None:
    """Append the rendered summary, naming the failure when it cannot be written."""
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    except OSError as error:
        emit("mutation_preflight_summary_error", str(error), stream=sys.stderr)


def _write_json(output_dir: Path, report: Report) -> None:
    """Write the machine-readable report, naming the failure when it cannot be."""
    document = {
        "lane": report.lane,
        "helper_python": report.runtimes.helper,
        "project_python": report.runtimes.project,
        "result": report.verdict,
        "phases": [phase_envelope(result) for result in report.results],
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "mutation-preflight.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        emit("mutation_preflight_report_error", str(error), stream=sys.stderr)


def write_report(output_dir: Path, report: Report) -> None:
    """Write the JSON report and append the Markdown summary, when asked to.

    Parameters
    ----------
    output_dir : Path
        Where the JSON report and the check logs are written.
    report : Report
        The run to publish.

    """
    _write_json(output_dir, report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    _append_summary(Path(summary), render_summary(report))


def _declaration_phases(
    root: Path, declaration: Declaration, project_python: str, output_dir: Path
) -> typ.Iterator[PhaseResult]:
    """Yield the phases a parsed declaration enables, running each when reached."""
    yield run_native_phase(root, declaration, project_python)
    yield run_sandbox_phase(root, declaration)
    yield run_commands_phase(root, declaration)
    yield run_checks_phase(root, declaration, output_dir)


def _all_phases(
    root: Path, loaded: Loaded, runtimes: Runtimes, output_dir: Path
) -> typ.Iterator[PhaseResult]:
    """Yield every phase in order, running each only when it is reached."""
    yield manifest_phase(loaded)
    yield run_interpreter_phase(root, runtimes)
    if loaded.declaration is not None:
        yield from _declaration_phases(
            root, loaded.declaration, runtimes.project, output_dir
        )


def checked_phases(
    root: Path, loaded: Loaded, runtimes: Runtimes, output_dir: Path
) -> list[PhaseResult]:
    """Run every phase, emitting each as it finishes.

    Parameters
    ----------
    root : Path
        The checkout being validated.
    loaded : Loaded
        What reading the declaration produced.
    runtimes : Runtimes
        Both interpreters, reported separately.
    output_dir : Path
        Where the check logs are written.

    Returns
    -------
    list[PhaseResult]
        Every phase that ran, in order.

    """
    results: list[PhaseResult] = []
    for result in _all_phases(root, loaded, runtimes, output_dir):
        emit("mutation_preflight_phase", phase_envelope(result))
        results.append(result)
    return results
