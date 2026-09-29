"""Name each `generate-coverage` step a workflow holds, as one leg.

A repository measuring on more than one platform or feature set runs several
generators in one job, and a pairing has to name which. A leg is named
`workflow-file:job-id:step`, where the step is its `name`, else its `id`,
else its position in the job.
"""

from __future__ import annotations

import typing as typ

from .publisher import COVERAGE_ACTION, invokes
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    from .loading import Document


class Leg(typ.NamedTuple):
    """One generator step, with where it sits."""

    ident: str
    document: Document
    job: dict[str, object]
    step: dict[str, object]


def leg_id(file: str, job_id: str, step: dict[str, object], index: int) -> str:
    """Return a leg's name.

    Examples
    --------
    >>> leg_id("ci.yml", "test", {"name": "Measure"}, 3)
    'ci.yml:test:Measure'
    >>> leg_id("ci.yml", "test", {"id": "cov"}, 3)
    'ci.yml:test:cov'
    >>> leg_id("ci.yml", "test", {}, 3)
    'ci.yml:test:#3'

    """
    label = step.get("name") or step.get("id") or f"#{index}"
    return f"{file}:{job_id}:{label}"


def generator_legs(file: str, document: Document) -> list[Leg]:
    """Return every `generate-coverage` step of a workflow as a leg.

    Parameters
    ----------
    file : str
        The workflow's file name, which begins each leg's name.
    document : Document
        The workflow to read.

    Returns
    -------
    list[Leg]
        The legs, in document order.

    """
    return [
        Leg(leg_id(file, job_id, step, index), document, job, step)
        for job_id, job in jobs(document).items()
        for index, step in enumerate(steps(job))
        if invokes(step, COVERAGE_ACTION)
    ]
