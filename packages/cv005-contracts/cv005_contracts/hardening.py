"""Hold the publisher's and the pull-request lanes' jobs to least privilege.

The other rules fix what these jobs run. These fix what the jobs may do
besides: the upload job reads the repository and nothing more, its checkout
leaves no credential behind, and a pull-request lane's coverage cannot fail
green, be switched off by a condition, or publish its report some other way.
"""

from __future__ import annotations

import typing as typ

from .expressions import ConditionError, conjuncts
from .publisher import COVERAGE_ACTION, invokes, upload_job
from .reading import jobs, steps

if typ.TYPE_CHECKING:
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


def lane_hardening_violations(closure: dict[str, Document]) -> list[str]:
    """Keep each pull-request coverage lane read-only and impossible to skip.

    Parameters
    ----------
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.

    Returns
    -------
    list[str]
        Every lane whose coverage could fail green, be switched off, hold
        write access, or publish its report through `upload-artifact`.

    """
    located = _coverage_steps(closure)
    found = [problem for place in located for problem in _lane_violations(*place)]
    reports = {str(_input(step, "output-path")) for *_, step in located}
    return found + _artefact_uploads(closure, reports)


def _coverage_steps(
    closure: dict[str, Document],
) -> list[tuple[str, Document, dict[str, object], dict[str, object]]]:
    """Return each lane coverage step with its workflow name, document and job."""
    return [
        (name, document, job, step)
        for name, document in sorted(closure.items())
        for job in jobs(document).values()
        for step in steps(job)
        if invokes(step, COVERAGE_ACTION)
    ]


def _lane_violations(
    name: str, document: Document, job: dict[str, object], step: dict[str, object]
) -> list[str]:
    """Report one lane's coverage step that can fail green or hold write access."""
    problems = [
        (
            "must not continue on error",
            _continues_on_error(step) or _continues_on_error(job),
        ),
        (
            "may carry only the pull-request guard as a condition",
            not (_only_guarded(step) and _only_guarded(job)),
        ),
        (
            f"job permissions must be exactly {READ_ONLY}",
            _effective_permissions(document, job) != READ_ONLY,
        ),
    ]
    return [f"{name}: coverage {problem}" for problem, failed in problems if failed]


def _only_guarded(holder: dict[str, object]) -> bool:
    """Return whether a condition is absent or exactly the pull-request guard."""
    if "if" not in holder:
        return True
    try:
        return frozenset(conjuncts(holder["if"])) == PULL_REQUEST_GUARD
    except ConditionError:
        return False


def _artefact_uploads(closure: dict[str, Document], reports: set[str]) -> list[str]:
    """Report a pull-request step publishing a coverage report as an artefact.

    `publish-artefact: 'false'` keeps the shared action from uploading the
    report; a separate `upload-artifact` step would publish it anyway.
    """
    return [
        f"{name}: must not upload the coverage report {report!r} as an artefact"
        for name, document in sorted(closure.items())
        for job in jobs(document).values()
        for step in steps(job)
        if invokes(step, ARTEFACT_ACTION)
        for report in sorted(reports)
        if report != "None" and report in str(_input(step, "path"))
    ]
