"""Follow a workflow's calls through this tree, and refuse what it cannot read.

A called workflow runs in its caller's event context, so a rule asked of
a caller is asked of everything it reaches. A local composite action runs
in its caller's job, so it is followed too. A call this checkout cannot
read, such as a local call at a ref, is refused rather than read as
compliant.
"""

from __future__ import annotations

import typing as typ
from pathlib import PurePosixPath

from .loading import Document, WorkflowReadingError
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Where a same-repository reusable workflow lives.
WORKFLOW_DIRECTORY: typ.Final[str] = ".github/workflows/"


def local_callee(reference: str, repository: str) -> str | None:
    """Return the workflow file a job-level `uses:` names in this tree.

    Matched by shape: strip a leading `./` or `$/` and ask whether the
    rest is a file under the workflow directory. A reference to another
    repository is not followed, since its content is not here.

    Parameters
    ----------
    reference : str
        The `uses:` reference to classify.
    repository : str
        The owner and name of the repository the workflow belongs to.

    Returns
    -------
    str | None
        The local workflow's file name, or None if `reference` does not
        name one.

    Raises
    ------
    WorkflowReadingError
        If a local spelling carries an `@ref`, or the reference names this
        repository qualified by a ref: either runs a version of the file
        this checkout does not hold, so following it would prove nothing.

    Examples
    --------
    >>> local_callee("$/.github/workflows/x.yml", "leynos/example")
    'x.yml'
    >>> local_callee("leynos/other/.github/workflows/x.yml@main", "leynos/example")

    """
    if _names_this_repository(reference, repository):
        message = f"{reference!r} calls this repository at a ref; use `./`"
        raise WorkflowReadingError(message)
    path = PurePosixPath(reference.removeprefix("./").removeprefix("$/"))
    directory = PurePosixPath(WORKFLOW_DIRECTORY)
    if path == directory or not path.is_relative_to(directory):
        return None
    if "@" in str(path):
        message = f"{reference!r} is a local call carrying an `@ref`"
        raise WorkflowReadingError(message)
    return str(path.relative_to(directory))


def _skip_none(_holder: dict[str, object]) -> bool:
    """Skip no job or step, so every local call is followed."""
    return False


def called_workflows(
    document: Document,
    repository: str,
    skip: cabc.Callable[[dict[str, object]], bool] = _skip_none,
) -> frozenset[str]:
    """Return the same-repository workflows one document's jobs call.

    A job for which `skip` answers true is left out, so a caller can follow
    only the calls that can run on the event it is asking about.

    Parameters
    ----------
    document : Document
        The workflow document to read.
    repository : str
        The owner and name of the repository the workflow belongs to.
    skip : cabc.Callable[[dict[str, object]], bool], optional
        Called on each job; a job it answers true for is not followed.

    Returns
    -------
    frozenset[str]
        The same-repository workflow file names called by `document`.

    """
    references = (job.get("uses") for job in _followed_jobs(document, skip))
    return _resolved(references, lambda reference: local_callee(reference, repository))


def _followed_jobs(
    document: Document, skip: cabc.Callable[[dict[str, object]], bool]
) -> list[dict[str, object]]:
    """Return the jobs of a document that `skip` does not leave out."""
    return [job for job in jobs(document).values() if not skip(job)]


def _resolved(
    references: cabc.Iterable[object], resolve: cabc.Callable[[str], str | None]
) -> frozenset[str]:
    """Return what each string reference resolves to, dropping remote ones."""
    names = (resolve(item) for item in references if isinstance(item, str))
    return frozenset(name for name in names if name is not None)


def local_action(reference: str, repository: str) -> str | None:
    """Return the tree path a step-level `uses:` names, or None for a remote one.

    The path is the key `actions.read_actions` files the action under.

    Parameters
    ----------
    reference : str
        The step's `uses:` reference.
    repository : str
        The owner and name of the repository the workflow belongs to.

    Returns
    -------
    str | None
        The action's path from the repository root, or None if `reference`
        does not name a local action.

    Raises
    ------
    WorkflowReadingError
        If a local spelling carries an `@ref`, or the reference names this
        repository qualified by a ref: either runs a version of the action
        this checkout does not hold, so following it would prove nothing.

    Examples
    --------
    >>> local_action("./.github/actions/build", "leynos/example")
    '.github/actions/build'
    >>> local_action("actions/checkout@v4", "leynos/example")

    """
    if _names_this_repository(reference, repository):
        message = f"{reference!r} runs this repository's action at a ref; use `./`"
        raise WorkflowReadingError(message)
    if not reference.startswith(("./", "$/")):
        return None
    if "@" in reference:
        message = f"{reference!r} is a local action carrying an `@ref`"
        raise WorkflowReadingError(message)
    return PurePosixPath(reference.removeprefix("$/")).as_posix()


def _names_this_repository(reference: str, repository: str) -> bool:
    """Return whether a reference names this repository by owner and name."""
    target = reference.casefold().split("@", 1)[0]
    own = repository.casefold()
    return target == own or target.startswith(f"{own}/")


def called_actions(
    document: Document,
    repository: str,
    skip: cabc.Callable[[dict[str, object]], bool] = _skip_none,
) -> frozenset[str]:
    """Return the local actions one document's steps run.

    A local composite action runs in its caller's job with the caller's
    secrets, so the closure follows it as it follows a called workflow.
    `skip` is asked about each job and each step, since a guard on either
    keeps the action from running.

    Parameters
    ----------
    document : Document
        The workflow document to read.
    repository : str
        The owner and name of the repository the workflow belongs to.
    skip : cabc.Callable[[dict[str, object]], bool], optional
        Called on each job and step; one it answers true for is not followed.

    Returns
    -------
    frozenset[str]
        The paths of the local actions `document`'s steps run.

    """
    references = (
        step.get("uses")
        for job in _followed_jobs(document, skip)
        for step in steps(job)
        if not skip(step)
    )
    return _resolved(references, lambda reference: local_action(reference, repository))


def reachable(
    documents: dict[str, Document],
    seeds: list[str],
    repository: str,
    skip: cabc.Callable[[dict[str, object]], bool] = _skip_none,
) -> dict[str, Document]:
    """Return the seed workflows and every local workflow or action they run.

    A called workflow runs in its caller's event context, so whatever a
    rule asks of the caller it must also ask of everything the caller
    reaches.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    seeds : list[str]
        The file names to start the closure from.
    repository : str
        The owner and name of the repository the workflows belong to.
    skip : cabc.Callable[[dict[str, object]], bool], optional
        Called on each job; a job it answers true for is not followed.

    Returns
    -------
    dict[str, Document]
        The seeds and every workflow they reach, by file name.

    Raises
    ------
    WorkflowReadingError
        If a call names a workflow or action this tree does not hold.

    """
    pending = list(seeds)
    found: dict[str, Document] = {}
    while pending:
        name = pending.pop()
        if name in found:
            continue
        if name not in documents:
            message = f"a workflow calls {name}, which does not exist"
            raise WorkflowReadingError(message)
        found[name] = documents[name]
        pending.extend(called_workflows(documents[name], repository, skip))
        pending.extend(called_actions(documents[name], repository, skip))
    return found
