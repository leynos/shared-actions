"""Shared reading boundary for the runner-placement and ceiling contracts.

This module holds the pieces several `test_runner_placement.py` siblings
need in common: the workflow directory and the validated YAML boundary
that turns a workflow file into typed data. The label vocabulary and the
exemption mappings live in `_workflow_policy.py` and are re-exported
here, so a contract reads one module. It
is deliberately not a test module — its name does not start with
`test_` — so pytest does not collect it and a change here cannot itself
become a passing or failing test.

Keeping this reading boundary in one place means every sibling module
sees the same validated shape rather than repeating `yaml.safe_load`
and re-deriving what a job or a workflow document looks like.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from ._workflow_policy import (
    CALLER_JOBS as CALLER_JOBS,
)
from ._workflow_policy import (
    FORK_FALLBACK_EXEMPTIONS as FORK_FALLBACK_EXEMPTIONS,
)
from ._workflow_policy import (
    FORK_GUARD_DISPATCH_ESCAPE as FORK_GUARD_DISPATCH_ESCAPE,
)
from ._workflow_policy import (
    FORK_GUARD_ESCAPES as FORK_GUARD_ESCAPES,
)
from ._workflow_policy import (
    FORK_GUARD_EVENT_ESCAPE as FORK_GUARD_EVENT_ESCAPE,
)
from ._workflow_policy import (
    FORK_SKIP_GUARD as FORK_SKIP_GUARD,
)
from ._workflow_policy import (
    HOSTED_LINUX as HOSTED_LINUX,
)
from ._workflow_policy import (
    HOSTED_LINUX_EXEMPTIONS as HOSTED_LINUX_EXEMPTIONS,
)
from ._workflow_policy import (
    LINUX_ONLY_JOBS as LINUX_ONLY_JOBS,
)
from ._workflow_policy import (
    RECOGNIZED_LINUX_LABELS as RECOGNIZED_LINUX_LABELS,
)
from ._workflow_policy import (
    RECOGNIZED_OTHER_LABELS as RECOGNIZED_OTHER_LABELS,
)

# Re-exported so a contract reads one module; see `_workflow_policy.py`.
from ._workflow_policy import (
    UBICLOUD_LINUX as UBICLOUD_LINUX,
)
from .workflow_yaml import load_workflow as load_strict_yaml
from .workflow_yaml import workflow_paths

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"

#: A `runs-on` that defers to the job's matrix, captured exactly. A
#: looser pattern would read `${{ matrix.os }}-latest` as a bare matrix
#: reference and lose the suffix.
MATRIX_REFERENCE: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}$"
)


#: The body of one GitHub Actions job. `runs-on`, `timeout-minutes` and
#: `if` are not valid Python identifiers, so this TypedDict is written in
#: the functional form; ruff's UP013 would otherwise rewrite it to the
#: class form, which cannot spell those keys at all.
JobBody = typ.TypedDict(
    "JobBody",
    {
        "uses": str,
        "runs-on": object,
        "if": str,
        "name": str,
        "timeout-minutes": int,
        "strategy": "cabc.Mapping[str, object]",
        "steps": "list[cabc.Mapping[str, object]]",
    },
    total=False,
)


class WorkflowDocument(typ.TypedDict, total=False):
    """The one field this module reads from a workflow document.

    Every key GitHub Actions accepts at the top level, such as `on`, is
    an identifier except this one, so the class form applies here per
    ruff's UP013, and the rest of the document is read as untyped YAML
    because nothing else in this module needs it.
    """

    jobs: cabc.Mapping[str, JobBody]


def load_workflow(
    name: str, *, directory: Path = WORKFLOWS_DIRECTORY
) -> WorkflowDocument:
    """Return the parsed workflow named *name*, validated at the boundary.

    The file is read through `workflow_yaml.load_workflow`, whose loader
    refuses a key declared twice. `yaml.safe_load` keeps the last of two
    `runs-on` or `timeout-minutes` keys silently, so the placement and
    ceiling rules would pass a workflow GitHub rejects. The parse returns
    `object`, so both the document and its `jobs` entry are checked here,
    once, rather than trusted by every reader downstream.

    Parameters
    ----------
    name : str
        The workflow's file name.
    directory : Path
        Where to find it; `.github/workflows` unless a test supplies its
        own.

    Returns
    -------
    WorkflowDocument
        The parsed document, known to be a mapping whose `jobs`, if
        present, is a mapping too.

    Raises
    ------
    ValueError
        If the file cannot be read, is not valid YAML, or declares a key
        twice. The message names the file.
    TypeError
        If the document is not a mapping, its `jobs` entry is absent,
        null, empty or not a mapping, or any job is not a mapping under a
        string id.
    """
    document = load_strict_yaml(directory / name)
    match document:
        case dict():
            pass
        case _:
            msg = f"{name}: workflow document is not a mapping: {document!r}"
            raise TypeError(msg)
    # PyYAML reads the bare key `on` as the boolean True, so the raw
    # mapping is read once here rather than through the typed view.
    raw = typ.cast("dict[typ.Hashable, object]", document)
    jobs = raw.get("jobs")
    match jobs:
        case dict() if jobs:
            pass
        case _:
            # A workflow holds one or more jobs. Absent, null or empty,
            # `jobs` would give every rule here nothing to inspect, and
            # each would pass by inspecting nothing.
            msg = f"{name}: 'jobs' is not a non-empty mapping: {jobs!r}"
            raise TypeError(msg)
    # Every entry is checked here too, so a scalar job fails at the
    # boundary naming the file, not later at some reader's `.get()`.
    for job_id, body in jobs.items():
        if not isinstance(job_id, str) or not isinstance(body, dict):
            msg = f"{name}: job {job_id!r} is not a mapping: {body!r}"
            raise TypeError(msg)
    return typ.cast("WorkflowDocument", document)


def workflow_document_on(document: WorkflowDocument) -> object:
    """Return the raw `on` trigger value from *document*.

    PyYAML reads the bare key `on` as the boolean `True`, so the lookup
    tries both spellings against the untyped view of the document.

    Parameters
    ----------
    document : WorkflowDocument
        A document returned by `load_workflow`.

    Returns
    -------
    object
        The trigger value as written: a string, a list or a mapping, or
        None when the document declares no triggers.
    """
    raw = typ.cast("dict[typ.Hashable, object]", document)
    return raw.get(True, raw.get("on"))


def workflow_names(directory: Path = WORKFLOWS_DIRECTORY) -> list[str]:
    """Return every workflow file name, sorted.

    Listed through `workflow_yaml.workflow_paths`, the same boundary the
    other workflow contracts use: its suffix match ignores case, so a
    `.YML` file is not skipped, and a directory that cannot be listed
    raises rather than yielding nothing for every rule to pass over.

    Parameters
    ----------
    directory : Path
        The workflows directory; `.github/workflows` unless a test
        supplies its own.

    Returns
    -------
    list of str
        The names of the workflow files in *directory*.

    Raises
    ------
    ValueError
        If *directory* is missing or cannot be listed.
    """
    return sorted(path.name for path in workflow_paths(directory) if path.is_file())


def jobs(
    name: str, *, directory: Path = WORKFLOWS_DIRECTORY
) -> cabc.Iterator[tuple[str, JobBody]]:
    """Yield each job id and body in the workflow named *name*.

    Parameters
    ----------
    name : str
        The workflow's file name.
    directory : Path
        Where to find it.

    Yields
    ------
    tuple of (str, JobBody)
        The job's id and its body, in document order.
    """
    yield from load_workflow(name, directory=directory)["jobs"].items()


def all_jobs(directory: Path = WORKFLOWS_DIRECTORY) -> list[tuple[str, str]]:
    """Return every (workflow, job id) pair in *directory*, sorted.

    Parameters
    ----------
    directory : Path
        The workflows directory.

    Returns
    -------
    list of tuple of (str, str)
        One pair per job across every workflow file.
    """
    return sorted(
        (name, job_id)
        for name in workflow_names(directory)
        for job_id, _ in jobs(name, directory=directory)
    )


def runner_job_ids(directory: Path = WORKFLOWS_DIRECTORY) -> list[tuple[str, str]]:
    """Return every pair for a job that occupies a runner.

    Parameters
    ----------
    directory : Path
        The workflows directory.

    Returns
    -------
    list of tuple of (str, str)
        `all_jobs()` without the jobs in `CALLER_JOBS`, which call a
        reusable workflow and occupy no runner of their own.
    """
    return [pair for pair in all_jobs(directory) if pair not in CALLER_JOBS]


def identifier(*parts: str) -> str:
    """Return a readable pytest id for *parts*.

    Parameters
    ----------
    *parts : str
        The pieces to join, such as a workflow name and a job id.

    Returns
    -------
    str
        The pieces joined with `::`.
    """
    return "::".join(parts)


def label_token(label: str) -> re.Pattern[str]:
    r"""Return a pattern matching *label* as a whole token.

    The delimiters are explicit rather than `\b`, because a word
    boundary sits inside a hyphenated label and would find
    `ubuntu-latest` in a longer name that merely contains it.

    Parameters
    ----------
    label : str
        The runner label to find.

    Returns
    -------
    re.Pattern of str
        A pattern matching *label* only where no label character touches
        either end.
    """
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(label)}(?![A-Za-z0-9_-])")
