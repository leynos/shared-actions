"""Running the phases and publishing what each one decided.

Every phase is emitted as it finishes, so a crash in a later phase still leaves
the earlier verdicts in the log. That is the point of an envelope that does not
depend on mutation results existing.
"""

from __future__ import annotations

import dataclasses as dc
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

_CHECKS_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so the declared checks were not run: they "
    "would execute the project under the interpreter that phase rejected"
)


@dc.dataclass(frozen=True, slots=True)
class Run:
    """What one preflight run checks, and where its evidence goes.

    Attributes
    ----------
    root : Path
        The checkout being validated.
    runtimes : Runtimes
        Both interpreters, reported separately.
    output_dir : Path
        Where the check logs are written.
    mutmut_version : str
        The mutmut release the run selects, whose copy contract the sandbox
        and checks phases use.

    """

    root: Path
    runtimes: Runtimes
    output_dir: Path
    mutmut_version: str


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


def _append_summary(path: Path, rendered: str) -> str:
    """Append the rendered summary, returning why it could not be written."""
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    except OSError as error:
        emit("mutation_preflight_summary_error", str(error), stream=sys.stderr)
        return f"the job summary {str(path)!r} could not be written: {error}"
    return ""


def _write_json(output_dir: Path, report: Report) -> str:
    """Write the machine-readable report, returning why it could not be written."""
    document = {
        "lane": report.lane,
        "helper_python": report.runtimes.helper,
        "project_python": report.runtimes.project,
        "result": report.verdict,
        "phases": [phase_envelope(result) for result in report.results],
    }
    destination = output_dir / "mutation-preflight.json"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        emit("mutation_preflight_report_error", str(error), stream=sys.stderr)
        return f"the report {str(destination)!r} could not be written: {error}"
    return ""


def write_report(output_dir: Path, report: Report) -> tuple[str, ...]:
    """Write the JSON report and append the Markdown summary, when asked to.

    Parameters
    ----------
    output_dir : Path
        Where the JSON report and the check logs are written.
    report : Report
        The run to publish.

    Returns
    -------
    tuple[str, ...]
        Why the diagnostics could not be published, or an empty tuple when
        they were. A run whose evidence cannot be read has not passed, so the
        caller fails it rather than exiting successfully with nothing to show.

    """
    written = _write_json(output_dir, report)
    problems = [written] if written else []
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        problem = _append_summary(Path(summary), render_summary(report))
        if problem:
            problems.append(problem)
    return tuple(problems)


def _declaration_phases(
    run: Run, declaration: Declaration, *, interpreter_ok: bool
) -> typ.Iterator[PhaseResult]:
    """Yield the phases a parsed declaration enables, running each when reached.

    The phases that execute the project's own environment are gated on the
    interpreter verdict: a probe or a check run under a rejected interpreter
    would certify an environment the mutation run will not use.
    """
    yield run_native_phase(
        run.root, declaration, run.runtimes.project, execute_probe=interpreter_ok
    )
    yield run_sandbox_phase(run.root, declaration, run.mutmut_version)
    yield run_commands_phase(run.root, declaration)
    if interpreter_ok:
        yield run_checks_phase(
            run.root, declaration, run.output_dir, run.mutmut_version
        )
        return
    yield PhaseResult("checks", SKIPPED, (), {"reason": _CHECKS_BLOCKED})


def _all_phases(run: Run, loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield every phase in order, running each only when it is reached."""
    yield manifest_phase(loaded)
    interpreter = run_interpreter_phase(run.root, run.runtimes)
    yield interpreter
    if loaded.declaration is not None:
        yield from _declaration_phases(
            run, loaded.declaration, interpreter_ok=interpreter.status != FAILED
        )


def checked_phases(run: Run, loaded: Loaded) -> list[PhaseResult]:
    """Run every phase, emitting each as it finishes.

    Parameters
    ----------
    run : Run
        The checkout, both interpreters, the output directory and the selected
        mutmut release.
    loaded : Loaded
        What reading the declaration produced.

    Returns
    -------
    list[PhaseResult]
        Every phase that ran, in order.

    """
    results: list[PhaseResult] = []
    for result in _all_phases(run, loaded):
        emit("mutation_preflight_phase", phase_envelope(result))
        results.append(result)
    return results
