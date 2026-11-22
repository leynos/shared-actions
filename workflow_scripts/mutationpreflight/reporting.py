"""Running the phases and publishing what each one decided.

Every phase is emitted as it finishes, so a crash in a later phase still leaves
the earlier verdicts in the log. That is the point of an envelope that does not
depend on mutation results existing.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from .checks import run_checks_phase
from .commands import run_commands_phase
from .console import emit
from .constants import FAILED, SKIPPED, SUCCEEDED
from .interpreters import run_interpreter_phase
from .models import Declaration, Loaded, PhaseResult, Report
from .native import (
    changed_lock_phase,
    judge_native,
    judged_identity,
    lock_identity,
    probe_blocked,
    probe_native,
    with_probe,
)
from .sandbox import run_sandbox_phase

if typ.TYPE_CHECKING:
    from .native import LockIdentity
    from .ports import Run

_NO_DECLARATION: typ.Final[str] = (
    "the repository declares no mutation environment requirements, so "
    "only the interpreter was checked"
)

_REFUSED_DECLARATION: typ.Final[str] = (
    "the declaration at {source} was refused, so no requirement it names was checked"
)

#: The phases a parsed declaration enables, in the order they run. The native
#: judgement runs last, after everything that can rewrite the checkout: the
#: declared checks run there on the cargo lane, so a lock they moved must be
#: seen by the judgement rather than judged before them. A run with no accepted
#: declaration reports each phase as skipped, so a report never omits a phase
#: because the declaration that would have named it was absent.
_DECLARATION_PHASES: typ.Final[tuple[str, ...]] = (
    "sandbox",
    "commands",
    "checks",
    "native",
)

_CHECKS_BLOCKED: typ.Final[str] = (
    "the interpreter phase failed, so the declared checks were not run: they "
    "would execute the project under the interpreter that phase rejected"
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


def write_report(run: Run, report: Report) -> tuple[str, ...]:
    """Append the Markdown summary, then write the JSON report.

    The summary is appended first so the JSON report can record whether it was
    published. The JSON is the machine-readable verdict, and a verdict that
    claimed a pass while its summary had failed would be misleading.

    Parameters
    ----------
    run : Run
        The run whose summary path and output directory receive the report.
    report : Report
        The run to publish.

    Returns
    -------
    tuple[str, ...]
        Why the diagnostics could not be published, or an empty tuple when
        they were. A run whose evidence cannot be read has not passed, so the
        caller fails it rather than exiting successfully with nothing to show.

    """
    problems: list[str] = []
    if run.summary_path:
        problem = run.files.append_summary(
            Path(run.summary_path), render_summary(report)
        )
        if problem is not None:
            problems.append(problem)
    written = run.files.write_report(run.output_dir, _report_document(report, problems))
    if written is not None:
        problems.append(written)
    return tuple(problems)


def _skipped_phases(loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield the declaration phases as skipped, naming why none of them ran.

    Without this, a repository that declares nothing would produce a report
    mentioning only the manifest and the interpreter: a reader could not tell
    whether the missing phases passed, failed, or were never applicable.
    """
    if loaded.source is None:
        reason = _NO_DECLARATION
    else:
        reason = _REFUSED_DECLARATION.format(source=loaded.source)
    for name in _DECLARATION_PHASES:
        yield PhaseResult(name, SKIPPED, (), {"reason": reason})


def _lock_moved(before: LockIdentity | None, after: LockIdentity | None) -> bool:
    """Return whether the declared checks moved the lock, or lost its identity.

    A baseline the reading never established is not a change: the judgement
    reports a lock it could not read or a package it did not find on its own.
    An identity established before the checks that cannot be established after
    them is a change: the checks removed the very record the judgement had
    proved, so a probe must not run on top of it.
    """
    if before is None:
        return False
    if after is None:
        return True
    return before != after


def _declaration_phases(
    run: Run, declaration: Declaration, *, interpreter_ok: bool
) -> typ.Iterator[PhaseResult]:
    """Yield the phases a parsed declaration enables, running each when reached.

    The phases that execute the project's own environment are gated on the
    interpreter verdict: a probe or a check run under a rejected interpreter
    would certify an environment the mutation run will not use.

    The native judgement runs last. The declared checks run in the checkout on
    the cargo lane, after the lock was read and before the mutation run starts,
    so a check that relocked the project would leave an earlier judgement
    describing an environment the run no longer uses. The identity the lock held
    before the checks is therefore captured first and compared with the identity
    the judgement reads after them: a lock that moved fails the native phase
    rather than certifying a pin the run will not use. The probe is composed
    onto that verdict only where the interpreter it would run under was
    accepted, and the judgement is passed to it so the lock it ran against is
    the one the probe must leave behind.
    """
    native = declaration.native
    before = lock_identity(run, native)
    yield run_sandbox_phase(run, declaration)
    yield run_commands_phase(run, declaration, probe_versions=interpreter_ok)
    if interpreter_ok:
        yield run_checks_phase(run, declaration)
    else:
        yield PhaseResult("checks", SKIPPED, (), {"reason": _CHECKS_BLOCKED})
    judged = judge_native(run, declaration)
    after = judged_identity(judged)
    if native is not None and _lock_moved(before, after):
        yield changed_lock_phase(judged, native, before, after)
        return
    probe = (
        probe_native(run, declaration, judged)
        if interpreter_ok
        else probe_blocked(declaration)
    )
    yield with_probe(judged, probe)


def _all_phases(run: Run, loaded: Loaded) -> typ.Iterator[PhaseResult]:
    """Yield every phase in order, running each only when it is reached."""
    yield manifest_phase(loaded)
    interpreter = run_interpreter_phase(run)
    yield interpreter
    if loaded.declaration is None:
        yield from _skipped_phases(loaded)
        return
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
