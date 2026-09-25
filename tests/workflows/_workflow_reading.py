"""Shared reading boundary for the runner-placement and ceiling contracts.

This module holds the pieces several `test_runner_placement.py` siblings
need in common: the workflow directory and label vocabulary, the
exemption mappings that name a deliberate exception to a rule, and the
validated YAML boundary that turns a workflow file into typed data. It
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

from .workflow_yaml import load_workflow as load_strict_yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"

#: The Ubicloud shape every migrated Linux lane starts on. The recipe
#: allows a larger shape only on measured disk or wall-time evidence, and
#: this repository has produced none, so the larger labels are absent
#: from the recognized set below and a move to one would fail here.
UBICLOUD_LINUX: typ.Final[str] = "ubicloud-standard-2"

#: The GitHub-hosted Linux label. Present only where an exemption below
#: says why.
HOSTED_LINUX: typ.Final[str] = "ubuntu-latest"

#: Every runner label this repository is allowed to name, as exact
#: tokens. An unrecognized label fails rather than being classified by
#: the shape of its name, because "starts with ubicloud" would accept a
#: shape nobody measured and "contains ubuntu" would accept
#: `ubicloud-standard-2-ubuntu-2404` as a GitHub-hosted runner.
RECOGNIZED_LINUX_LABELS: typ.Final[frozenset[str]] = frozenset(
    {UBICLOUD_LINUX, HOSTED_LINUX}
)
RECOGNIZED_OTHER_LABELS: typ.Final[frozenset[str]] = frozenset(
    {"macos-15", "windows-latest", "windows-11-arm"}
)

_MUTATION_REASON: typ.Final[str] = (
    "Mutation lanes stay GitHub-hosted: they are scheduled, they never "
    "block a developer, and public-repository minutes are free there."
)

#: Linux jobs that must stay on a GitHub-hosted runner, each with the
#: reason. A job absent from this mapping must run on Ubicloud.
HOSTED_LINUX_EXEMPTIONS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    (
        "test-export-ubicloud-cache-credentials.yml",
        "refuses-a-github-hosted-runner",
    ): (
        "The job exists to prove the action fails closed against a real "
        "GitHub-hosted cache endpoint. On Ubicloud the endpoint is the one "
        "the action accepts, so the job would pass without testing anything."
    ),
    ("mutation-cargo.yml", "detect"): _MUTATION_REASON,
    ("mutation-cargo.yml", "mutants"): _MUTATION_REASON,
    ("mutation-cargo.yml", "summarize"): _MUTATION_REASON,
    ("mutation-mutmut.yml", "mutants"): _MUTATION_REASON,
    ("dependabot-automerge.yml", "automerge"): (
        "A delayed-comment lane that waits on other checks rather than "
        "computing anything, and never blocks a developer."
    ),
}

#: Jobs that must run on Linux on every arm, each with the reason. The
#: placement rule skips a job with no Linux arm, which is right for a
#: macOS or Windows lane and wrong for one of these: moved to
#: `macos-15`, it would stop being checked at all while the only run of
#: its work left Linux.
LINUX_ONLY_JOBS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("ci.yml", "coverage"): (
        "The only lane that measures coverage, and the one the CodeScene "
        "upload and the ratchet read. The baseline is keyed by runner.os, so "
        "moving it off Linux would compare against a baseline nothing writes."
    ),
}

#: Lanes that answer the fork problem by skipping rather than falling
#: back, with the reason and the guard that has to be there.
#:
#: Falling back is the default because a skip leaves an external
#: contribution with no Linux CI. It is the wrong answer only where the
#: hosted runner cannot prove what the job exists to prove, in which
#: case a fallback would make the job pass while testing nothing.
FORK_FALLBACK_EXEMPTIONS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("test-ubicloud-sccache-proxy.yml", "reaches-the-proxy"): (
        "The job exists to prove sccache reaches Ubicloud's cache proxy, "
        "which is observable on an Ubicloud runner and nowhere else. A "
        "fallback to a GitHub-hosted runner would leave it green and "
        "proving nothing, so it skips a fork's pull request instead."
    ),
    ("test-upload-codescene-coverage.yml", "cold-runner-contract"): (
        "The job installs the pinned CLI through the repository's own "
        "action tree, which a fork's pull request cannot reach in the shape "
        "the proof needs, so the job's own guard skips a fork's pull "
        "request rather than falling back."
    ),
}

#: The head-repository comparison an exempt lane must guard itself with.
#: Keyed on the head repository rather than on `github.repository`,
#: which is the base repository and matches a fork's pull request too.
FORK_SKIP_GUARD: typ.Final[str] = (
    "github.event.pull_request.head.repo.full_name == github.repository"
)

#: The one disjunct that may sit beside the guard. A workflow serving a
#: dispatch as well as a pull request has to let the dispatch through,
#: and a dispatch runs on the base repository, so there is no fork to
#: keep out on that arm. Written out exactly rather than matched loosely,
#: because this is the single escape the rule allows and a near miss
#: should fail rather than be accepted as close enough.
FORK_GUARD_EVENT_ESCAPE: typ.Final[str] = "github.event_name != 'pull_request'"

#: The other way the same arm is written: naming the one event the lane
#: also serves rather than excluding pull requests. A fork reaches this
#: repository through a pull request and through nothing else, so an arm
#: that requires a dispatch admits no fork either. Both spellings are
#: written out in full, and an arm naming any other event is refused,
#: because `github.event_name == 'pull_request'` has the same shape and
#: the opposite meaning.
FORK_GUARD_DISPATCH_ESCAPE: typ.Final[str] = "github.event_name == 'workflow_dispatch'"

#: The complete set of arms that keep a fork out without the comparison.
FORK_GUARD_ESCAPES: typ.Final[frozenset[str]] = frozenset(
    {FORK_GUARD_EVENT_ESCAPE, FORK_GUARD_DISPATCH_ESCAPE}
)

#: Jobs that only call another workflow. They occupy no runner and can
#: carry neither a label nor a ceiling.
CALLER_JOBS: typ.Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("dependabot-automerge-caller.yml", "automerge"),
        ("mutation-testing-caller.yml", "mutation"),
        ("test-dependabot-automerge.yml", "automerge"),
        ("test-mutation-cargo.yml", "mutation"),
        ("test-mutation-mutmut.yml", "mutation"),
    }
)

#: Both extensions GitHub accepts for a workflow file. Scanning only
#: `.yml` would let a `.yaml` workflow past every rule in this module
#: while the module still claimed to be exhaustive.
WORKFLOW_SUFFIXES: typ.Final[tuple[str, ...]] = (".yml", ".yaml")

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
        If the document, or its `jobs` entry, is not a mapping.
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
        case None | dict():
            pass
        case _:
            msg = f"{name}: 'jobs' is not a mapping: {jobs!r}"
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


def workflow_names() -> list[str]:
    """Return every workflow file name, sorted.

    Returns
    -------
    list of str
        The names of the files under `.github/workflows` with a workflow
        suffix.
    """
    return sorted(
        path.name
        for path in WORKFLOWS_DIRECTORY.iterdir()
        if path.suffix in WORKFLOW_SUFFIXES and path.is_file()
    )


def jobs(name: str) -> cabc.Iterator[tuple[str, JobBody]]:
    """Yield each job id and body in the workflow named *name*.

    Parameters
    ----------
    name : str
        The workflow's file name.

    Yields
    ------
    tuple of (str, JobBody)
        The job's id and its body, in document order.
    """
    yield from (load_workflow(name).get("jobs") or {}).items()


def all_jobs() -> list[tuple[str, str]]:
    """Return every (workflow, job id) pair in the repository, sorted.

    Returns
    -------
    list of tuple of (str, str)
        One pair per job across every workflow file.
    """
    return sorted(
        (name, job_id) for name in workflow_names() for job_id, _ in jobs(name)
    )


def runner_job_ids() -> list[tuple[str, str]]:
    """Return every pair for a job that occupies a runner.

    Returns
    -------
    list of tuple of (str, str)
        `all_jobs()` without the jobs in `CALLER_JOBS`, which call a
        reusable workflow and occupy no runner of their own.
    """
    return [pair for pair in all_jobs() if pair not in CALLER_JOBS]


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
