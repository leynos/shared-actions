"""What a workflow declares, read for the main-owned coverage boundary.

The readers behind `test_main_owned_coverage`, which asserts them against
this repository's own workflows, and `test_workflow_boundary_reading`, which
drives them on inputs written for the purpose. They live apart from both so
that neither module carries a second subject, and so that a clause added to
the contract does not grow the module that reads YAML.

What starts a workflow is read in `workflow_triggers`, which this module
builds its closure and its publisher reading on.

Nothing here touches the filesystem, at import or otherwise, except the two
digest scans, which take the directory to scan so a caller can build one and
read it through `workflow_yaml`'s boundary, so a failure is a ``ValueError``
naming the path.
Every other reader takes parsed documents; the contract's fixture is where
this repository's workflows are read.
"""

from __future__ import annotations

import posixpath
import re
import typing as typ

from .workflow_triggers import pushes_to_main, starts_on_pull_request
from .workflow_yaml import read_workflow_text, workflow_paths

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from .test_coverage_timeout_tiers import WorkflowDocument, WorkflowJob

#: Prefixes a `uses:` may carry before a path in this repository. They are
#: stripped, not enumerated as the only spellings: a reference is local when
#: what remains resolves under the path being asked about, so a spelling this
#: list does not name is still read by its shape.
SELF_PREFIXES: typ.Final[tuple[str, ...]] = ("./", "$/")
#: The local coverage action every lane invokes, without its prefix.
COVERAGE_ACTION: typ.Final[str] = ".github/actions/generate-coverage"
#: The local CodeScene action; only the publisher may invoke it.
CODESCENE_ACTION: typ.Final[str] = "upload-codescene-coverage"
#: The language this repository's coverage measures, and the package the
#: measurement is scoped to. The scope is half of the baseline contract: it
#: fixes the population the percentages describe, so a lane that dropped it
#: would compare a whole-repository figure with a scoped baseline.
COVERAGE_LANGUAGE: typ.Final[str] = "python"
COVERAGE_SCOPE: typ.Final[str] = "workflow_scripts"
#: The CodeScene credential. No pull-request-reachable workflow names it.
CODESCENE_CREDENTIAL: typ.Final[str] = "CS_ACCESS_TOKEN"
#: The CodeScene CLI subcommands that contact the service. `install` and
#: `version` reach nothing, and the uploader's cold-runner proof needs them
#: on every pull request that changes it, so the client's name alone is not
#: the boundary: what it is asked to do is.
CODESCENE_SERVICE_COMMANDS: typ.Final[tuple[str, ...]] = (
    "cs-coverage check",
    "cs-coverage upload",
)
#: The one mode of the CodeScene action that contacts nothing.
OFFLINE_MODE: typ.Final[str] = "install"
#: The CodeScene service itself. Forbidding the action, the client and the
#: credential closes the known doors, not the lane: a step can reach the
#: project API with a plain `curl`, naming none of the three, and nothing
#: fails. The host is what the boundary is actually about.
CODESCENE_HOST: typ.Final[str] = "codescene.io"
#: The installer-script digest input, rejected by the action when non-empty.
CHECKSUM_INPUT: typ.Final[str] = "installer-checksum"
#: The repository variable the deleted digest refresher used to write.
DIGEST_VARIABLE: typ.Final[str] = "CODESCENE_CLI_SHA256"
#: The path of a `uses:` naming a workflow in this repository, without its
#: self-repository prefix.
LOCAL_WORKFLOW_PATH: typ.Final[str] = ".github/workflows/"
#: A ``runs-on`` expression naming exactly one matrix dimension.
_MATRIX_REFERENCE: typ.Final[re.Pattern[str]] = re.compile(
    r"\$\{\{\s*matrix(?:\.([A-Za-z0-9_-]+)|\[\s*(['\"])([A-Za-z0-9_-]+)\2\s*\])\s*\}\}"
)

#: A matrix reference anywhere inside an expression, in either spelling.
#: Its quoted key, as in `matrix['os']`, names a dimension, not a runner,
#: so references are removed before quoted literals are read as runners.
_MATRIX_ANYWHERE: typ.Final[re.Pattern[str]] = re.compile(
    r"matrix(?:\.[A-Za-z0-9_-]+|\[\s*(['\"])[A-Za-z0-9_-]+\1\s*\])"
)

#: A single-quoted string literal inside an expression. The fork fallback,
#: `${{ ...fork && 'ubuntu-latest' || 'ubicloud-standard-2' }}`, names its
#: runners this way, and each arm is a runner the job can land on.
_QUOTED_LITERAL: typ.Final[re.Pattern[str]] = re.compile(r"'([^']+)'")


def _self_reference_target(uses: str, path: str) -> str | None:
    """Return what ``uses`` names under ``path`` in this repository, or ``None``.

    The reference is matched by its shape. A leading self-repository prefix
    and any ``@ref`` suffix are dropped, the remainder is normalized, and it
    is local when it lies under ``path``. An enumerated prefix list, or a
    refusal of the ``@ref`` a `$/` reference should not carry, is a reader
    that stops recognizing a call the moment its spelling changes, and an
    unrecognized call takes its target out of every boundary in silence.
    Recognizing one spelling too many only adds prohibitions, which fail
    loudly, so the reading errs that way.

    Examples
    --------
    >>> _self_reference_target("./.github/workflows/a.yml@main", ".github/workflows/")
    'a.yml'
    >>> _self_reference_target("octo/r/.github/workflows/a.yml", ".github/workflows/")
    """
    reference = uses.split("@", 1)[0]
    for prefix in SELF_PREFIXES:
        reference = reference.removeprefix(prefix)
    if not reference:
        return None
    normalized = posixpath.normpath(reference)
    base = path.rstrip("/")
    if normalized == base:
        return ""
    if normalized.startswith(f"{base}/"):
        return normalized.removeprefix(f"{base}/")
    return None


def _self_reference(uses: str, path: str) -> bool:
    """Return whether ``uses`` names ``path`` in this repository."""
    return _self_reference_target(uses, path) is not None


def _called_workflow(job: WorkflowJob) -> str | None:
    """Return the local workflow file name a job delegates to, if any."""
    return _self_reference_target(str(job.get("uses", "")), LOCAL_WORKFLOW_PATH)


def _callees(documents: cabc.Mapping[str, WorkflowDocument], name: str) -> set[str]:
    """Return the local workflows one workflow's jobs delegate to."""
    called = (_called_workflow(job) for job in _jobs(documents.get(name, {})).values())
    return {callee for callee in called if callee in documents}


def pull_request_reachable(
    documents: cabc.Mapping[str, WorkflowDocument],
) -> set[str]:
    """Return every workflow a pull request can reach, directly or via a call.

    Reachability is transitive through ``uses:`` at job level: a caller a
    pull request starts drags the reusable workflow it calls into the same
    boundary. Without that, a CodeScene call moved one file away would
    satisfy a per-file reading while still running on every pull request.

    Parameters
    ----------
    documents : Mapping[str, WorkflowDocument]
        Parsed workflows keyed by file name.

    Returns
    -------
    set[str]
        File names.
    """
    reached = {
        name for name, document in documents.items() if starts_on_pull_request(document)
    }
    pending = list(reached)
    while pending:
        for callee in _callees(documents, pending.pop()) - reached:
            reached.add(callee)
            pending.append(callee)
    return reached


def _walk_strings(node: object) -> cabc.Iterator[str]:
    """Yield every string reachable from *node*, by its YAML shape."""
    if isinstance(node, dict):
        return _walk_mapping(node)
    if isinstance(node, list):
        return _walk_sequence(node)
    return _walk_scalar(node)


def _walk_mapping(node: cabc.Mapping[typ.Any, typ.Any]) -> cabc.Iterator[str]:
    """Yield a mapping's keys as well as its values.

    A credential arrives as ``CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}``
    and the key is the half that names it. A key is read as a scalar, so the
    boolean an unquoted ``on:`` resolves to contributes no text.
    """
    for key, value in node.items():
        yield from _walk_scalar(key)
        yield from _walk_strings(value)


def _walk_sequence(node: cabc.Sequence[typ.Any]) -> cabc.Iterator[str]:
    """Yield every string reachable from a sequence's items."""
    for item in node:
        yield from _walk_strings(item)


def _walk_scalar(node: object) -> cabc.Iterator[str]:
    """Yield a scalar's text, unless it carries none.

    ``None`` is how YAML spells an empty value, and a boolean is what an
    unquoted ``on:`` or ``true`` becomes; neither is text a step can act on.
    """
    if node is None or isinstance(node, bool):
        return
    yield str(node)


def effective_text(document: cabc.Mapping[typ.Any, typ.Any]) -> str:
    """Return every string a workflow can act on, comments excluded.

    The scan used to read the file text, so that a `run:` step could not hide
    a call the parse would miss. It read comments too, and a comment contacts
    nothing: explaining in prose why a lane must not name the credential made
    the lane name it. Walking the parse keeps the `run:` bodies, which is the
    hiding place that mattered, and drops what GitHub itself drops.

    Environment keys are included as well as values, because a credential
    arrives as ``CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}`` and the
    key is the half that names it.

    The whole document is walked, not a list of the sections known to run
    something. A workflow-level ``defaults.run.shell`` wraps every `run:`
    step in the file, so a reading of ``jobs`` and ``env`` alone passed a
    shell that curls the host; a reading that names its sections misses the
    next one GitHub adds.

    Parameters
    ----------
    document : Mapping
        One parsed workflow.

    Returns
    -------
    str
        The strings, newline separated.

    Examples
    --------
    >>> effective_text({"jobs": {"a": {"steps": [{"run": "echo hi"}]}}})
    'echo hi'
    """
    return "\n".join(_walk_strings(document))


def names_the_codescene_host(document: cabc.Mapping[typ.Any, typ.Any]) -> bool:
    """Return whether a workflow can reach ``codescene.io``.

    The comparison folds case, because a DNS name is case-insensitive and a
    lane that capitalised the host would otherwise pass. The credential is
    compared exactly elsewhere, because an environment variable name is
    case-sensitive; the two are deliberately not folded together, and this is
    the reason.

    The fold lives here rather than in the assertion so that removing it
    fails a test. Spelled at the call site, every case would have folded the
    text itself before comparing, and the rule would have survived its own
    mutation.

    Parameters
    ----------
    document : Mapping
        One parsed workflow.

    Returns
    -------
    bool
        True when the effective text names the host in any case.

    Examples
    --------
    >>> names_the_codescene_host(
    ...     {"jobs": {"a": {"steps": [{"run": "curl https://CodeScene.IO"}]}}}
    ... )
    True
    """
    return CODESCENE_HOST in effective_text(document).lower()


def _steps(job: WorkflowJob) -> list[dict[str, typ.Any]]:
    """Return a job's steps, ignoring anything that is not a mapping."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _jobs(document: cabc.Mapping[typ.Any, typ.Any]) -> dict[str, WorkflowJob]:
    """Return a workflow's jobs, keyed by identifier."""
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(name): job for name, job in jobs.items() if isinstance(job, dict)}


def coverage_steps(
    document: cabc.Mapping[typ.Any, typ.Any],
) -> list[tuple[str, dict[str, typ.Any]]]:
    """Return every ``generate-coverage`` step, with its job identifier."""
    return [
        (name, step)
        for name, job in _jobs(document).items()
        for step in _steps(job)
        if _self_reference(str(step.get("uses", "")), COVERAGE_ACTION)
    ]


def codescene_steps(
    document: cabc.Mapping[typ.Any, typ.Any],
) -> list[dict[str, typ.Any]]:
    """Return every step invoking the CodeScene action."""
    return [
        step
        for job in _jobs(document).values()
        for step in _steps(job)
        if CODESCENE_ACTION in str(step.get("uses", ""))
    ]


def upload_steps(
    document: cabc.Mapping[typ.Any, typ.Any],
) -> list[dict[str, typ.Any]]:
    """Return every CodeScene step running in ``upload`` mode."""
    return [
        step
        for step in codescene_steps(document)
        if str((step.get("with") or {}).get("mode", "")) == "upload"
    ]


def _dimension_values(matrix: cabc.Mapping[str, typ.Any], name: str) -> list[str]:
    """Return one matrix dimension's values, ``include`` entries among them."""
    listed = matrix.get(name)
    values = listed if isinstance(listed, list) else [] if listed is None else [listed]
    included = [
        entry[name]
        for entry in matrix.get("include") or []
        if isinstance(entry, dict) and name in entry
    ]
    return [str(value) for value in [*values, *included]]


def _matrix_labels(job: WorkflowJob, expression: str) -> list[str]:
    """Return the values a job's matrix can substitute into ``runs-on``.

    ``${{ matrix.os }}`` names one dimension, and only that dimension's values
    are runners; a ``python-version`` beside it is not a platform. An
    expression of any other shape is resolved to every value in the matrix,
    because which ones it selects cannot be read, and over-reading only adds
    platforms the publisher must also cover, which fails loudly. Every quoted
    literal such an expression names is a runner too: the fork fallback
    selects between two of them, and a reader that dropped them would find
    no runner at all and excuse the lane from the ratchet.

    Examples
    --------
    >>> job = {"strategy": {"matrix": {"os": ["windows-latest"], "py": ["3.13"]}}}
    >>> _matrix_labels(job, "${{ matrix.os }}")
    ['windows-latest']
    >>> _matrix_labels({}, "${{ fork && 'ubuntu-latest' || 'ubicloud-standard-2' }}")
    ['ubuntu-latest', 'ubicloud-standard-2']
    >>> _matrix_labels(job, "${{ matrix['os'] }}")
    ['windows-latest']
    >>> _matrix_labels(job, "${{ fork && 'ubuntu-latest' || matrix['os'] }}")
    ['ubuntu-latest', 'windows-latest', '3.13']
    """
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    reference = _MATRIX_REFERENCE.fullmatch(expression.strip())
    if reference is not None:
        return _dimension_values(matrix, reference.group(1) or reference.group(3))
    return [
        *_QUOTED_LITERAL.findall(_MATRIX_ANYWHERE.sub("", expression)),
        *(
            str(value)
            for values in matrix.values()
            if isinstance(values, list)
            for value in values
        ),
    ]


def _declared_labels(raw: object) -> list[str]:
    """Return the labels a ``runs-on`` value names, in any of its three forms.

    GitHub accepts a scalar, a sequence, and a mapping carrying ``labels``,
    ``group``, or both. A group names no platform, so its name is carried as
    an unrecognized label rather than dropped.
    """
    if isinstance(raw, dict):
        raw = raw.get("labels", raw.get("group"))
    if isinstance(raw, list):
        return [str(value) for value in raw]
    return [] if raw is None else [str(raw)]


def _runner_labels(job: WorkflowJob) -> list[str]:
    """Return the runner labels a job can run on.

    Each label naming a matrix value is resolved through the job's own
    matrix, whichever form ``runs-on`` takes, so a Windows or macOS lane
    declared that way is not read as one indeterminate runner and quietly
    excused from the ratchet.

    Examples
    --------
    >>> matrix = {"os": ["windows-latest"]}
    >>> job = {"runs-on": ["${{ matrix.os }}"], "strategy": {"matrix": matrix}}
    >>> _runner_labels(job)
    ['windows-latest']
    """
    return [
        resolved
        for label in _declared_labels(job.get("runs-on"))
        for resolved in (_matrix_labels(job, label) if "${{" in label else [label])
    ]


def _platform_of(label: str) -> str:
    """Return the platform keyword a runner label names.

    The label is returned unchanged when it names none of them, so an
    unrecognized runner is carried into the comparison rather than dropped.
    Ubicloud's runners are Linux, so ``ubicloud-standard-2`` reads as
    ``ubuntu``: the ratchet baseline is keyed by ``runner.os``, which is
    ``Linux`` on both.

    Examples
    --------
    >>> _platform_of("ubicloud-standard-2")
    'ubuntu'
    """
    for keyword in ("ubuntu", "linux", "ubicloud", "windows", "macos"):
        if keyword in label:
            return keyword if keyword in {"windows", "macos"} else "ubuntu"
    return label


def platforms(document: cabc.Mapping[typ.Any, typ.Any], job_name: str) -> set[str]:
    """Return the platform keywords a job's runners resolve to.

    Parameters
    ----------
    document : Mapping
        The parsed workflow.
    job_name : str
        The job's identifier.

    Returns
    -------
    set[str]
        One of ``ubuntu``, ``windows``, ``macos``, or the raw value when
        it matches none of them.

    Examples
    --------
    >>> document = {"jobs": {"a": {"runs-on": "windows-latest"}}}
    >>> platforms(document, "a")
    {'windows'}
    """
    job = _jobs(document).get(job_name, {})
    return {_platform_of(label) for label in _runner_labels(job)}


def _ratcheted_platforms(
    document: cabc.Mapping[typ.Any, typ.Any],
) -> set[str]:
    """Return the platforms whose coverage lanes arm the ratchet."""
    return {
        platform
        for job_name, step in coverage_steps(document)
        if str((step.get("with") or {}).get("with-ratchet", "")) == "true"
        for platform in platforms(document, job_name)
    }


def _publishers(
    documents: cabc.Mapping[str, WorkflowDocument],
) -> list[str]:
    """Return the workflows that publish this repository's coverage.

    A publisher pushes to ``main`` **and serves no pull request**. The
    second half matters: a repository's `ci.yml` commonly declares both
    triggers, and the looser reading would make one file simultaneously
    required to upload and forbidden from uploading.
    """
    return sorted(
        name
        for name, document in documents.items()
        if pushes_to_main(document)
        and not starts_on_pull_request(document)
        and upload_steps(document)
    )


def digest_offenders(directory: Path) -> dict[str, list[str]]:
    """Return the workflows in *directory* that carry the deleted digest path.

    Both extensions are read. A scan over one of them is blind to a workflow
    spelled with the other, and GitHub runs either.

    Parameters
    ----------
    directory : Path
        Where the workflows live.

    Returns
    -------
    dict[str, list[str]]
        File name to the markers found in it.

    Raises
    ------
    ValueError
        When the directory cannot be listed or a workflow cannot be read.
    """
    found = {
        path.name: [
            marker
            for marker in (f"{CHECKSUM_INPUT}:", DIGEST_VARIABLE)
            if marker in read_workflow_text(path)
        ]
        for path in workflow_paths(directory)
    }
    return {name: markers for name, markers in found.items() if markers}


def digest_refreshers(directory: Path) -> list[str]:
    """Return any digest-refresher workflow in *directory*, under either name.

    Raises
    ------
    ValueError
        When the directory cannot be listed.
    """
    return sorted(
        path.name
        for path in workflow_paths(directory)
        if path.stem == "get-codescene-sha"
    )
