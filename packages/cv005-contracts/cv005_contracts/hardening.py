"""Hold the publisher's and the pull-request lanes' jobs to least privilege.

The other rules fix what these jobs run. These fix what the jobs may do
besides: the upload job reads the repository and nothing more, its checkout
leaves no credential behind, and a pull-request lane's coverage cannot fail
green, be switched off by a condition, or publish its report some other way.
"""

from __future__ import annotations

import re
import typing as typ

from .closure import called_actions
from .expressions import ConditionError, conjuncts
from .legs import Leg, generator_legs
from .publisher import invokes, upload_job
from .reading import jobs, steps
from .report_paths import could_hold_the_report, entries_of

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .loading import Document

#: The only token scope the upload job and a coverage lane's job need.
READ_ONLY: typ.Final[dict[str, str]] = {"contents": "read"}
CHECKOUT_ACTION: typ.Final[str] = "actions/checkout"
ARTEFACT_ACTION: typ.Final[str] = "actions/upload-artifact"

#: The one condition a pull-request coverage step or its job may carry: the
#: guard that keeps a lane which also runs on a push off the baseline.
PULL_REQUEST_GUARD: typ.Final[frozenset[str]] = frozenset(
    {
        "github.event_name == 'pull_request'",
    }
)


def _continues_on_error(holder: dict[str, object]) -> bool:
    """Return whether a step or job may fail without failing its run."""
    return holder.get("continue-on-error", False) is not False


def _effective_permissions(document: Document, job: dict[str, object]) -> object:
    """Return the permissions a job runs with: its own, else the workflow's."""
    return job["permissions"] if "permissions" in job else document.get("permissions")


def publisher_hardening_violations(document: Document) -> list[str]:
    """Hold the upload job to read-only access and credential-free checkouts.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every departure from the upload job's least-privilege shape.

    """
    job = upload_job(document)
    found = (
        []
        if _effective_permissions(document, job) == READ_ONLY
        else [f"the upload job's permissions must be exactly {READ_ONLY}"]
    )
    return found + [
        "a publisher checkout must set persist-credentials: false"
        for step in steps(job)
        if invokes(step, CHECKOUT_ACTION) and not _keeps_no_credentials(step)
    ]


def _input(step: dict[str, object], name: str) -> object:
    """Return one `with` input of a step, or None when it has none."""
    inputs = step.get("with")
    return inputs.get(name) if isinstance(inputs, dict) else None


def _keeps_no_credentials(step: dict[str, object]) -> bool:
    """Return whether a checkout step sets `persist-credentials: false`."""
    return _input(step, "persist-credentials") is False


def lane_hardening_violations(
    closure: dict[str, Document],
    declared: cabc.Mapping[str, frozenset[str]] | None = None,
    repository: str = "",
) -> list[str]:
    """Keep each pull-request coverage lane read-only and impossible to skip.

    Parameters
    ----------
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.
    declared : Mapping[str, frozenset[str]] | None, optional
        The extra conditions each declared lane leg may carry besides the
        pull-request guard, by leg name.
    repository : str, optional
        The owner and name of the repository, used to follow the local actions
        a lane's job runs. It is what lets a step naming this repository's own
        action at an `@ref` be refused rather than read as a remote action
        whose uploads go unjudged.

    Returns
    -------
    list[str]
        Every lane whose coverage could fail green, be switched off, hold
        write access, or publish its report through `upload-artifact`.

    """
    located = _coverage_legs(closure)
    extra = declared or {}
    found = [
        problem
        for name, leg in located
        for problem in _lane_violations(name, leg, extra.get(leg.ident, frozenset()))
    ]
    return found + _artefact_uploads(closure, located, repository)


def _coverage_legs(closure: dict[str, Document]) -> list[tuple[str, Leg]]:
    """Return each lane coverage leg with the name of the workflow holding it."""
    return [
        (name, leg)
        for name, document in sorted(closure.items())
        for leg in generator_legs(name, document)
    ]


def _lane_violations(name: str, leg: Leg, extra_terms: frozenset[str]) -> list[str]:
    """Report one lane's coverage step that can fail green or hold write access."""
    problems = [
        (
            "must not continue on error",
            _continues_on_error(leg.step) or _continues_on_error(leg.job),
        ),
        (
            "may carry only the pull-request guard as a condition",
            not (
                _only_guarded(leg.step, extra_terms) and _job_guarded(leg.job, leg.step)
            ),
        ),
        (
            f"job permissions must be exactly {READ_ONLY}",
            _effective_permissions(leg.document, leg.job) != READ_ONLY,
        ),
    ]
    return [f"{name}: coverage {problem}" for problem, failed in problems if failed]


def _only_guarded(
    holder: dict[str, object], extra_terms: frozenset[str] = frozenset()
) -> bool:
    """Return whether a condition is absent or the pull-request guard, and no more.

    `extra_terms` are the conditions a declared pairing says select this leg;
    they are held exactly, so any other term is still refused.
    """
    if "if" not in holder:
        return True
    try:
        return frozenset(conjuncts(holder["if"])) == PULL_REQUEST_GUARD | extra_terms
    except ConditionError:
        return False


#: A job condition that the pull-request guard already implies: the event is
#: not some other named event. It cannot switch the lane off on a pull request.
_OTHER_EVENT: typ.Final[re.Pattern[str]] = re.compile(
    r"^github\.event_name != '(?P<event>[a-z_]+)'$"
)


def _job_guarded(job: dict[str, object], step: dict[str, object]) -> bool:
    """Return whether a lane job's condition leaves its coverage step runnable.

    The job's condition is absent or the pull-request guard, as for a step.
    It may also exclude other named events (`github.event_name != 'schedule'`)
    when the step itself carries the pull-request guard: an event cannot be a
    pull request and another event, so the exclusion changes nothing for a
    pull request, and the job stays free to serve its other lanes.
    """
    if _only_guarded(job):
        return True
    if "if" not in step:
        return False
    try:
        terms = frozenset(conjuncts(job["if"]))
        carried = frozenset(conjuncts(step["if"]))
    except ConditionError:
        return False
    others = terms - PULL_REQUEST_GUARD
    return carried >= PULL_REQUEST_GUARD and all(
        (match := _OTHER_EVENT.match(term)) is not None
        and match["event"] != "pull_request"
        for term in others
    )


def _artefact_uploads(
    closure: dict[str, Document], located: list[tuple[str, Leg]], repository: str
) -> list[str]:
    """Report a lane step publishing the lane's own coverage report as an artefact.

    `publish-artefact: 'false'` keeps the shared action from uploading the
    report; a separate `upload-artifact` step would publish it anyway. Only a
    step on the runner that holds the report can select it, so the rule reads
    the lane job's own steps and the local actions that job runs. An upload in
    another job, or in a workflow with no coverage lane, sees a different
    workspace and cannot reach the report.
    """
    found: list[str] = []
    for name, steps_here, reports in _report_holders(closure, located, repository):
        found.extend(
            f"{name}: must not upload the coverage report {report!r} as an artefact"
            for step in steps_here
            if invokes(step, ARTEFACT_ACTION)
            for report in sorted(reports)
            if report != "None"
            and any(
                could_hold_the_report(entry, report)
                for entry in entries_of(_input(step, "path"))
            )
        )
    return found


def _report_holders(
    closure: dict[str, Document], located: list[tuple[str, Leg]], repository: str
) -> list[tuple[str, list[dict[str, object]], set[str]]]:
    """Return each place that runs beside a lane's report, with the reports.

    A place is a lane job's steps, filed under its workflow, or the steps of a
    local action that job runs, filed under the action's path. An action run
    by several lane jobs holds the reports of all of them.
    """
    holders: dict[str, tuple[list[dict[str, object]], set[str]]] = {}
    seen: set[int] = set()
    for name, leg in located:
        report = str(_input(leg.step, "output-path"))
        if id(leg.job) not in seen:
            seen.add(id(leg.job))
            holders[f"{name}#{id(leg.job)}"] = (steps(leg.job), set())
        holders[f"{name}#{id(leg.job)}"][1].add(report)
        for action in _local_actions(leg.job, closure, repository):
            slot = holders.setdefault(action, (_action_steps(closure[action]), set()))
            slot[1].add(report)
        for dependent in _persistent_dependents(leg.document, leg.job):
            key = f"{name}#{id(dependent)}"
            slot = holders.setdefault(key, (steps(dependent), set()))
            slot[1].add(report)
    return [
        (key.split("#", 1)[0], held, reports)
        for key, (held, reports) in sorted(holders.items())
    ]


def _persistent_dependents(
    document: Document, lane: dict[str, object]
) -> list[dict[str, object]]:
    """Return the jobs after the lane job that may share its workspace.

    A job that `needs` the lane job, directly or through others, and runs on a
    `self-hosted` runner may land on the runner the lane used, and GitHub does
    not promise a clean workspace there. It can then upload the lane's report
    as easily as the lane can. Jobs on hosted runners start clean, so only the
    self-hosted ones are read.
    """
    declared = jobs(document)
    lane_ids = {ident for ident, job in declared.items() if job is lane}
    reached = set(lane_ids)
    changed = True
    while changed:
        changed = False
        for ident, job in declared.items():
            if ident not in reached and reached & _needs(job):
                reached.add(ident)
                changed = True
    return [
        declared[ident]
        for ident in sorted(reached - lane_ids)
        if "self-hosted" in str(declared[ident].get("runs-on", "")).lower()
    ]


def _needs(job: dict[str, object]) -> set[str]:
    """Return the job identifiers a job waits for."""
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return {needs}
    return (
        {item for item in needs if isinstance(item, str)}
        if isinstance(needs, list)
        else set()
    )


def _action_steps(document: Document) -> list[dict[str, object]]:
    """Return every step of a local action's document."""
    return [step for job in jobs(document).values() for step in steps(job)]


def _local_actions(
    job: dict[str, object], closure: dict[str, Document], repository: str
) -> list[str]:
    """Return the closure's local actions a job runs, directly or through others."""
    pending = sorted(called_actions({"jobs": {"lane": job}}, repository))
    found: list[str] = []
    while pending:
        action = pending.pop()
        if action in found or action not in closure:
            continue
        found.append(action)
        pending.extend(sorted(called_actions(closure[action], repository)))
    return found
