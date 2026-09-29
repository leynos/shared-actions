"""Rules for the coverage lanes: pull requests ratchet, main writes the baseline.

generate-coverage saves its ratchet baseline on a push to main, so the
publisher must be the only lane that measures on that event, and every
pull-request lane must measure what the publisher measures: a lane
compiling a different selection compares a feature difference, not a
commit difference, and a number comes out either way.
"""

from __future__ import annotations

import typing as typ

from .closure import reachable
from .expressions import ConditionError, missing_terms
from .parity import inputs_of, is_false, ratchet_violations
from .publisher import COVERAGE_ACTION, action_steps, invokes
from .reading import jobs, steps, triggers

if typ.TYPE_CHECKING:
    from .loading import Document

PULL_REQUEST_GUARD: typ.Final[frozenset[str]] = frozenset(
    {
        "github.event_name == 'pull_request'",
    }
)


def pull_request_lane_violations(closure: dict[str, Document]) -> list[str]:
    """Require every pull-request lane to ratchet and publish nothing.

    Each job ratchets exactly one of its coverage legs, and no leg ships
    its report or sets `publish-baseline`, which could save a baseline from
    a pull request.

    Parameters
    ----------
    closure : dict[str, Document]
        The pull-request-reachable workflows, by file name.

    Returns
    -------
    list[str]
        Every violation of the pull-request lanes' ratchet-only shape.

    """
    lanes = [
        (name, step)
        for name, document in sorted(closure.items())
        for step in action_steps(document, COVERAGE_ACTION)
    ]
    if not lanes:
        return ["no pull-request workflow generates coverage"]
    found = [
        problem
        for name, document in sorted(closure.items())
        for problem in ratchet_violations(document, name)
    ]
    found += [
        f"{name}: generate-coverage must set publish-artefact: 'false'"
        for name, step in lanes
        if not is_false(inputs_of(step).get("publish-artefact"))
    ]
    return found + [
        f"{name}: generate-coverage may not set publish-baseline"
        for name, step in lanes
        if "publish-baseline" in inputs_of(step)
    ]


def _guarded_to_pull_requests(holder: dict[str, object]) -> bool:
    """Return whether a step or a job runs only for a pull request."""
    try:
        return not missing_terms(holder.get("if"), PULL_REQUEST_GUARD)
    except ConditionError:
        return False


def _push_coverage_steps(document: Document) -> list[dict[str, object]]:
    """Return the coverage steps that can run when a push starts the document.

    A job guarded to pull requests never runs on a push, and neither does
    any step in it, whatever the step's own condition says.

    Returns
    -------
    list[dict[str, object]]
        The coverage steps a push can run.

    """
    return [
        step
        for job in jobs(document).values()
        if not _guarded_to_pull_requests(job)
        for step in steps(job)
        if invokes(step, COVERAGE_ACTION) and not _guarded_to_pull_requests(step)
    ]


def second_writer_violations(
    documents: dict[str, Document], publisher: str, repository: str
) -> list[str]:
    """Refuse coverage on a push anywhere but the publisher.

    A lane running on both events would write a second baseline on every
    push to main, so each generate-coverage step that another push-started
    workflow reaches, itself or through a local reusable workflow it
    calls, must run for pull requests only. A guard on the step or on its
    job counts, and a call made from a job guarded to pull requests is not
    followed, since that job never runs on a push.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    publisher : str
        The publisher's file name.
    repository : str
        The owner and name of the repository the workflows belong to.

    Returns
    -------
    list[str]
        Every violation of the second-writer rule.

    """
    seeds = [
        name
        for name, document in documents.items()
        if name != publisher and "push" in triggers(document)
    ]
    closure = reachable(documents, seeds, repository, _guarded_to_pull_requests)
    return [
        f"{name}: generate-coverage can run on a push; guard it to pull requests"
        for name, document in sorted(closure.items())
        if name != publisher
        for _ in _push_coverage_steps(document)
    ]
