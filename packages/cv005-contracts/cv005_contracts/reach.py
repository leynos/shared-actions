"""Find what a pull request can reach, and refuse CodeScene anywhere in it.

The pull-request surface is a closure rather than a trigger list. A
workflow declaring only `workflow_call` still runs on a pull request when
a pull-request job calls it, and `secrets: inherit` hands it every secret,
so a reading that enumerated triggers alone could not see it at all.
"""

from __future__ import annotations

import re
import typing as typ

from .closure import reachable
from .expressions import expression_bodies
from .loading import Document, WorkflowReadingError
from .reading import PULL_REQUEST_TRIGGERS, jobs, texts, trigger_filters, triggers

#: Case-folded patterns no pull-request-reachable document may contain:
#: the CodeScene host (a DNS name, so case-insensitive), the credential,
#: the client, the shared uploader and the retired checksum plumbing.
#: They are prohibitions, so a false match fails loudly rather than
#: hiding anything.
CODESCENE_MARKERS: typ.Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"codescene\.io"),
    re.compile(r"cs_access_token"),
    re.compile(r"(?<![\w-])cs-coverage(?![\w-])"),
    re.compile(r"upload-codescene-coverage"),
    re.compile(r"codescene_cli_sha256"),
    re.compile(r"installer-checksum"),
)


def _is_chained_on_a_run(document: Document) -> bool:
    """Return whether a workflow runs after another workflow's run.

    A `workflow_run` workflow runs with secrets after whatever it names,
    which may be a pull-request workflow, so it is treated as reachable.

    Returns
    -------
    bool
        Whether the workflow declares `workflow_run`.

    """
    return "workflow_run" in triggers(document)


#: Push filters that confine a push trigger to the trunk. A tags-only push
#: is judged separately; the publisher rules read the same filters, so the
#: publisher can never seed the pull-request closure.
TRUNK_FILTERS: typ.Final[tuple[dict[str, object], ...]] = (
    {"branches": ["main"]},
    {"branches": "main"},
)


def _pushes_other_branches(document: Document) -> bool:
    """Return whether a push trigger can run for a branch other than main.

    A push to a pull request's branch runs the branch's own workflows
    with secrets, so a push not confined to `branches: [main]` or to tags
    alone is pull-request surface. An unrecognized filter fails closed.

    Returns
    -------
    bool
        Whether a push can run the workflow off the trunk.

    """
    if "push" not in triggers(document):
        return False
    filters = trigger_filters(document, "push")
    is_tags_only = bool(filters) and set(filters) <= {"tags", "tags-ignore"}
    return not is_tags_only and filters not in TRUNK_FILTERS


def is_pull_request_seed(document: Document) -> bool:
    """Return whether a workflow is started directly by a pull request.

    Parameters
    ----------
    document : Document
        The workflow document to read.

    Returns
    -------
    bool
        Whether the workflow is a pull-request seed.

    """
    return (
        bool(triggers(document) & PULL_REQUEST_TRIGGERS)
        or _is_chained_on_a_run(document)
        or _pushes_other_branches(document)
    )


def pull_request_closure(
    documents: dict[str, Document], repository: str
) -> dict[str, Document]:
    """Return every workflow a pull request can start, transitively.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    repository : str
        The owner and name of the repository the workflows belong to.

    Returns
    -------
    dict[str, Document]
        The pull-request-reachable workflows, by file name.

    Raises
    ------
    WorkflowReadingError
        If no workflow serves a pull request, which is the reader failing
        rather than the repository complying, or a call names a workflow
        this tree does not hold.

    """
    seeds = [name for name, doc in documents.items() if is_pull_request_seed(doc)]
    if not seeds:
        message = "no workflow serves a pull request; the trigger reader is broken"
        raise WorkflowReadingError(message)
    return reachable(documents, seeds, repository)


def codescene_contacts(document: Document) -> list[str]:
    """Return every key or scalar in a document naming CodeScene.

    The whole document is read, case-folded, so neither a workflow-level
    `defaults.run.shell` nor a callee's secret declaration can reach the
    service unseen.

    Parameters
    ----------
    document : Document
        The workflow document to read.

    Returns
    -------
    list[str]
        Every key or scalar found naming CodeScene.

    Examples
    --------
    >>> codescene_contacts({"jobs": {"a": {"env": {"U": "https://API.CodeScene.io"}}}})
    ['https://API.CodeScene.io']

    """
    return [
        text
        for text in texts(document)
        if any(marker.search(text.casefold()) for marker in CODESCENE_MARKERS)
    ]


#: A whole `${{ }}` expression, which is where Actions reads contexts.

#: A read of the `secrets` context that does not name one secret literally,
#: such as `toJSON(secrets)` or `secrets[format('CS_{0}', 'ACCESS_TOKEN')]`.
#: The named forms `secrets.NAME` and `secrets['NAME']` are what the
#: credential sweeps can see, so only they pass.
_UNNAMED_SECRETS: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.-])secrets(?![\w-])"
    r"(?!\s*\.\s*[a-z_][a-z0-9_]*)"
    r"(?!\s*\[\s*'[^']*'\s*\])"
)


def unnamed_secret_references(document: Document) -> list[str]:
    """Return every expression reading secrets without naming one literally.

    A sweep for the credential's name cannot see `toJSON(secrets)`, which
    hands over every secret, or a name assembled at run time, so a read of
    the context that names nothing is refused wherever such a sweep runs.

    Parameters
    ----------
    document : Document
        The workflow document to read.

    Returns
    -------
    list[str]
        Every expression reading `secrets` without naming one literally.

    Examples
    --------
    >>> unnamed_secret_references({"run": "echo ${{ toJSON(secrets) }}"})
    ['echo ${{ toJSON(secrets) }}']
    >>> unnamed_secret_references({"run": "echo ${{ secrets.OTHER }}"})
    []

    """
    return [
        text
        for text in texts(document)
        if any(
            _UNNAMED_SECRETS.search(body.casefold()) for body in expression_bodies(text)
        )
    ]


def inherited_secrets(document: Document) -> list[str]:
    """Return the jobs forwarding every secret with `secrets: inherit`.

    `inherit` names nothing, so a sweep for the credential's name cannot
    see what it hands over; a pull-request-reachable job may not use it.

    Parameters
    ----------
    document : Document
        The workflow document to read.

    Returns
    -------
    list[str]
        The identifiers of jobs declaring `secrets: inherit`.

    """
    return [
        name
        for name, job in jobs(document).items()
        if str(job.get("secrets", "")).strip() == "inherit"
    ]


def pull_request_violations(
    documents: dict[str, Document], repository: str
) -> list[str]:
    """Return every way the pull-request surface reaches CodeScene.

    Parameters
    ----------
    documents : dict[str, Document]
        Every parsed workflow, by file name.
    repository : str
        The owner and name of the repository the workflows belong to.

    Returns
    -------
    list[str]
        Every violation naming the workflow and what it found.

    """
    closure = pull_request_closure(documents, repository)
    return [
        f"{name}: {finding}"
        for name, document in sorted(closure.items())
        for finding in (
            *(f"names {text!r}" for text in codescene_contacts(document)),
            *(
                f"reads secrets without naming one: {text!r}"
                for text in unnamed_secret_references(document)
            ),
            *(
                f"job {job} uses secrets: inherit"
                for job in inherited_secrets(document)
            ),
        )
    ]
