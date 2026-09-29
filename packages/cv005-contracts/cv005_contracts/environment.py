"""Hold the CodeScene token's environment to the uploading job (CV-005).

The token belongs in the `codescene` environment, whose deployment policy
admits `main` alone; until the owner moves it there it remains a repository
secret, which the uploading job reads either way. So every job that invokes
the uploader declares that environment, no other job does, and no workflow a
pull request can start declares it in any job: a declaration there would let
branch code ask for the token. GitHub compares environment names without
case, so the rules do too, and a name computed by an expression is refused
because its placement cannot be proved.
"""

from __future__ import annotations

import typing as typ

from .publisher import UPLOAD_ACTION
from .reach import pull_request_closure
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .loading import Document

ENVIRONMENT: typ.Final[str] = "codescene"
MISSING: typ.Final[str] = f"the uploading job must declare `environment: {ENVIRONMENT}`"
STRAY: typ.Final[str] = f"declares `{ENVIRONMENT}` but uploads nothing"
REACHABLE: typ.Final[str] = (
    f"is reachable from a pull request and declares `{ENVIRONMENT}`"
)
UNRESOLVED: typ.Final[str] = (
    "names its environment with an expression, so its placement cannot be proved"
)


def environment_name(job: dict[str, object]) -> str | None:
    """Return the environment a job declares, from either accepted form.

    Parameters
    ----------
    job : dict[str, object]
        The job to read.

    Returns
    -------
    str | None
        The environment's name, or None when the job declares none.

    Examples
    --------
    >>> environment_name({"environment": "codescene"})
    'codescene'
    >>> environment_name({"environment": {"name": "codescene", "url": "x"}})
    'codescene'
    >>> environment_name({}) is None
    True

    """
    match job.get("environment"):
        case str() as name:
            return name
        case {"name": str() as name}:
            return name
        case _:
            return None


def declares_codescene(job: dict[str, object]) -> bool:
    """Return whether a job declares the `codescene` environment.

    GitHub treats environment names case-insensitively, so `CodeScene` is
    the same protected environment.

    Parameters
    ----------
    job : dict[str, object]
        The job to read.

    Returns
    -------
    bool
        True when the declared name folds to `codescene`.

    Examples
    --------
    >>> declares_codescene({"environment": "CodeScene"})
    True
    >>> declares_codescene({"environment": "production"})
    False

    """
    name = environment_name(job)
    return name is not None and name.casefold() == ENVIRONMENT


def has_unresolved_environment(job: dict[str, object]) -> bool:
    """Return whether a job computes its environment name with an expression.

    Parameters
    ----------
    job : dict[str, object]
        The job to read.

    Returns
    -------
    bool
        True when the declared name contains `${{`.

    Examples
    --------
    >>> has_unresolved_environment({"environment": {"name": "${{ 'codescene' }}"}})
    True
    >>> has_unresolved_environment({"environment": "codescene"})
    False

    """
    name = environment_name(job)
    return name is not None and "${{" in name


def uploads(job: dict[str, object]) -> bool:
    """Return whether a job has a step invoking the shared uploader.

    The action path must match exactly, at any ref, as the publisher
    contract matches it, so a look-alike action does not count.

    Parameters
    ----------
    job : dict[str, object]
        The job to read.

    Returns
    -------
    bool
        True when some step's `uses` names the upload action.

    Examples
    --------
    >>> uploads({"steps": [{"uses": f"{UPLOAD_ACTION}@v1"}]})
    True
    >>> uploads({"steps": [{"uses": f"{UPLOAD_ACTION}-check@v1"}]})
    False

    """
    return any(
        str(step.get("uses", "")).split("@", 1)[0] == UPLOAD_ACTION
        for step in steps(job)
    )


def _placed(
    documents: dict[str, Document], names: cabc.Iterable[str]
) -> list[tuple[str, dict[str, object]]]:
    """Return every job in the named workflows with its location.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    names : Iterable[str]
        The workflows to read.

    Returns
    -------
    list[tuple[str, dict[str, object]]]
        `"workflow:job"` and the job, for each job.

    """
    return [
        (f"{name}:{job_id}", job)
        for name in sorted(names)
        for job_id, job in jobs(documents[name]).items()
    ]


def environment_violations(
    documents: dict[str, Document], repository: str
) -> list[str]:
    """Report every departure from the `codescene` environment placement.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    repository : str
        The owner and name of the repository the workflows belong to.

    Returns
    -------
    list[str]
        One message per violation; empty when the placement holds.

    """
    placed = _placed(documents, documents)
    uploading = [(where, job) for where, job in placed if uploads(job)]
    if not uploading:
        return ["no workflow job invokes the CodeScene uploader"]
    problems = [
        f"{where}: {MISSING}" for where, job in uploading if not declares_codescene(job)
    ]
    problems.extend(
        f"{where} {STRAY}"
        for where, job in placed
        if not uploads(job) and declares_codescene(job)
    )
    problems.extend(
        f"{where} {UNRESOLVED}"
        for where, job in placed
        if has_unresolved_environment(job)
    )
    reachable = pull_request_closure(documents, repository)
    problems.extend(
        f"{where} {REACHABLE}"
        for where, job in _placed(reachable, reachable)
        if declares_codescene(job)
    )
    return problems
