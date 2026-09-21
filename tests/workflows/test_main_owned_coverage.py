"""Contract-test the repository's main-owned CodeScene coverage flow (CV-005).

Under main-owned coverage no workflow a pull request can start may contact
CodeScene: pull-request lanes generate ratcheted coverage locally, and one
push-to-main publisher owns both the upload and the ratchet baseline every
pull request compares against.

The workflows are **enumerated** rather than named. A workflow added later is
covered the day it appears, and a caller job that delegates to a local
reusable workflow carries pull-request reachability into that file, so a
CodeScene call cannot hide one `uses:` away from the trigger that starts it.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from .test_coverage_timeout_tiers import (
    WORKFLOWS_DIRECTORY,
    WorkflowDocument,
    WorkflowJob,
    workflow_documents,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The local coverage action every lane invokes.
COVERAGE_ACTION: typ.Final[str] = "./.github/actions/generate-coverage"
#: The local CodeScene action; only the publisher may invoke it.
CODESCENE_ACTION: typ.Final[str] = "upload-codescene-coverage"
#: The CodeScene credential. No pull-request-reachable workflow names it.
CODESCENE_CREDENTIAL: typ.Final[str] = "CS_ACCESS_TOKEN"
#: The CodeScene CLI. No pull-request-reachable workflow invokes it.
CODESCENE_CLI: typ.Final[str] = "cs-coverage"
#: The installer-script digest input, rejected by the action when non-empty.
CHECKSUM_INPUT: typ.Final[str] = "installer-checksum"
#: The repository variable the deleted digest refresher used to write.
DIGEST_VARIABLE: typ.Final[str] = "CODESCENE_CLI_SHA256"
#: Triggers a pull request can fire.
PULL_REQUEST_EVENTS: typ.Final[frozenset[str]] = frozenset(
    {"pull_request", "pull_request_target"}
)
#: The prefix of a `uses:` naming a workflow in this repository.
LOCAL_WORKFLOW_PREFIX: typ.Final[str] = "./.github/workflows/"

#: This repository's own workflows, parsed once.
THIS_REPOSITORY: typ.Final[dict[str, WorkflowDocument]] = workflow_documents()


def triggers(document: cabc.Mapping[typ.Any, typ.Any]) -> dict[str, typ.Any]:
    """Return a workflow's triggers, keyed by event name.

    PyYAML resolves an unquoted ``on:`` key to the boolean ``True``, so a
    reader that consults only the string key sees no triggers at all and
    every boundary drawn from it passes over an empty set. Both keys are
    read here, and the three spellings GitHub accepts (a mapping, a list,
    and a bare string) are normalised to a mapping.

    Parameters
    ----------
    document : Mapping
        One parsed workflow.

    Returns
    -------
    dict[str, Any]
        Event name to its configuration, which may be ``None``.

    Examples
    --------
    >>> triggers({True: {"pull_request": None}})
    {'pull_request': None}
    >>> triggers({"on": ["push", "workflow_dispatch"]})
    {'push': None, 'workflow_dispatch': None}
    """
    raw = document.get("on", document.get(True))
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(event): None for event in raw}
    if isinstance(raw, dict):
        return {str(event): value for event, value in raw.items()}
    return {}


def starts_on_pull_request(document: cabc.Mapping[typ.Any, typ.Any]) -> bool:
    """Return whether a pull request can start this workflow directly."""
    return bool(PULL_REQUEST_EVENTS & set(triggers(document)))


def pushes_to_main(document: cabc.Mapping[typ.Any, typ.Any]) -> bool:
    """Return whether a push to ``main`` starts this workflow.

    A ``push`` trigger with no branch filter runs on every branch, ``main``
    among them, so it counts.
    """
    if "push" not in triggers(document):
        return False
    configuration = triggers(document)["push"]
    if not isinstance(configuration, dict):
        return True
    branches = configuration.get("branches")
    if branches is None:
        return True
    return "main" in [str(branch) for branch in branches]


def _called_workflows(job: WorkflowJob) -> list[str]:
    """Return the local workflow file names a job delegates to."""
    uses = str(job.get("uses", ""))
    if not uses.startswith(LOCAL_WORKFLOW_PREFIX):
        return []
    return [uses.removeprefix(LOCAL_WORKFLOW_PREFIX).split("@")[0]]


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
        document = documents.get(pending.pop(), {})
        for job in (document.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for called in _called_workflows(job):
                if called in documents and called not in reached:
                    reached.add(called)
                    pending.append(called)
    return reached


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
        if str(step.get("uses", "")).startswith(COVERAGE_ACTION)
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


def platforms(document: cabc.Mapping[typ.Any, typ.Any], job_name: str) -> set[str]:
    """Return the platform keywords a job's runners resolve to.

    A ``runs-on`` naming a matrix value is resolved through the job's own
    matrix, so a Windows or macOS lane declared that way is not read as a
    single indeterminate runner and quietly excused from the ratchet.

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
    raw = job.get("runs-on")
    candidates: list[str] = []
    if isinstance(raw, str) and "${{" in raw:
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for values in matrix.values():
            if isinstance(values, list):
                candidates.extend(str(value) for value in values)
    elif isinstance(raw, list):
        candidates.extend(str(value) for value in raw)
    elif raw is not None:
        candidates.append(str(raw))
    resolved: set[str] = set()
    for candidate in candidates:
        for keyword in ("ubuntu", "linux", "windows", "macos"):
            if keyword in candidate:
                resolved.add("ubuntu" if keyword == "linux" else keyword)
                break
        else:
            resolved.add(candidate)
    return resolved


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


@pytest.fixture(name="documents")
def documents_fixture() -> dict[str, WorkflowDocument]:
    """Return this repository's parsed workflows."""
    return THIS_REPOSITORY


class TestPullRequestLanesNeverReachCodeScene:
    """No workflow a pull request can start may contact CodeScene."""

    def test_the_boundary_is_not_empty(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Name the lanes the boundary covers, so it cannot pass over nothing.

        Every assertion below quantifies over a set. A reader that resolved
        no triggers would make that set empty and satisfy all of them, which
        is the failure mode the ``on:`` boolean key produces.
        """
        reachable = pull_request_reachable(documents)
        assert "ci.yml" in reachable, (
            f"ci.yml must be pull-request reachable; reached {sorted(reachable)}"
        )

    def test_no_reachable_workflow_names_the_credential_or_the_cli(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Read the file text, not the parse, so a run step cannot hide a call."""
        offenders = {
            name: [
                marker
                for marker in (CODESCENE_CREDENTIAL, CODESCENE_CLI)
                if marker in (WORKFLOWS_DIRECTORY / name).read_text(encoding="utf-8")
            ]
            for name in sorted(pull_request_reachable(documents))
        }
        assert {name: found for name, found in offenders.items() if found} == {}

    def test_no_reachable_workflow_invokes_the_codescene_action(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A `uses:` is the other way a lane reaches CodeScene."""
        offenders = {
            name: len(codescene_steps(documents[name]))
            for name in sorted(pull_request_reachable(documents))
            if codescene_steps(documents[name])
        }
        assert offenders == {}


class TestPullRequestCoverageIsRatchetedAndUnpublished:
    """Pull-request coverage compares with the baseline and publishes nothing."""

    def test_at_least_one_lane_generates_coverage(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Pair the rule below with its presence half.

        ``every lane is ratcheted`` is satisfied by having no lanes at all.
        """
        lanes = [
            (name, job)
            for name in sorted(pull_request_reachable(documents))
            for job, _ in coverage_steps(documents[name])
        ]
        assert lanes, "no pull-request lane generates coverage"

    def test_every_lane_ratchets_and_withholds_the_artefact(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The publisher owns publication, so a lane keeps its report local."""
        readings = {
            f"{name}:{job}": (
                str((step.get("with") or {}).get("with-ratchet", "")),
                str((step.get("with") or {}).get("publish-artefact", "")),
            )
            for name in sorted(pull_request_reachable(documents))
            for job, step in coverage_steps(documents[name])
        }
        assert readings == dict.fromkeys(readings, ("true", "false"))


class TestMainOwnsPublication:
    """Exactly one push-to-main workflow uploads, under a guard and a lock."""

    def test_exactly_one_publisher(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """One file pushes to main, serves no pull request, and uploads."""
        assert _publishers(documents) == ["coverage-main.yml"]

    def test_no_other_workflow_uploads(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """An upload outside the publisher would race the baseline it writes."""
        uploaders = sorted(
            name for name, document in documents.items() if upload_steps(document)
        )
        assert uploaders == ["coverage-main.yml"]

    def test_the_upload_is_guarded_by_ref_and_credential(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A dispatch selects its own ref; the push filter says nothing about it.

        Without the ref half, a dispatch from a feature branch publishes that
        branch's coverage as the trunk's.
        """
        (publisher,) = _publishers(documents)
        (upload,) = upload_steps(documents[publisher])
        guard = str(upload.get("if", ""))
        assert "github.ref == 'refs/heads/main'" in guard, guard
        assert f"env.{CODESCENE_CREDENTIAL} != ''" in guard, guard

    def test_the_publisher_is_serialised(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Two overlapping main pushes must not race to write the baseline."""
        (publisher,) = _publishers(documents)
        concurrency = documents[publisher].get("concurrency")
        assert isinstance(concurrency, dict), concurrency
        assert str(concurrency.get("group", "")).strip() != ""

    def test_the_publisher_ratchets_every_platform_a_lane_ratchets(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The ratchet baseline is keyed by runner OS.

        A platform that ratchets on pull requests but never on the trunk push
        compares against a baseline nothing writes.
        """
        (publisher,) = _publishers(documents)
        lanes = {
            platform
            for name in sorted(pull_request_reachable(documents))
            for platform in _ratcheted_platforms(documents[name])
        }
        published = _ratcheted_platforms(documents[publisher])
        assert lanes, "no pull-request lane arms the ratchet"
        assert lanes <= published, f"unpublished baselines: {sorted(lanes - published)}"


class TestTheDeletedDigestPathStaysDeleted:
    """The installer-script digest and its refresher are gone for good."""

    def test_no_workflow_passes_a_checksum_or_reads_the_variable(self) -> None:
        """``installer-checksum`` is rejected when non-empty; the variable is dead."""
        offenders = {
            path.name: [
                marker
                for marker in (f"{CHECKSUM_INPUT}:", DIGEST_VARIABLE)
                if marker in path.read_text(encoding="utf-8")
            ]
            for path in sorted(WORKFLOWS_DIRECTORY.glob("*.yml"))
        }
        assert {name: found for name, found in offenders.items() if found} == {}

    def test_no_digest_refresher_workflow_exists(self) -> None:
        """Its only output was that variable (YAGNI ruling, 2026-09-18)."""
        assert not list(WORKFLOWS_DIRECTORY.glob("get-codescene-sha.y*ml"))


class TestTheTriggerReaderSeesBothKeys:
    """Drive the reader directly, on documents chosen rather than found.

    Parametrised over this repository's compliant workflows the reader would
    pass whether or not it understood the boolean key, because a reader that
    resolves nothing agrees with a repository that calls CodeScene nowhere.
    """

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param({True: {"pull_request": None}}, True, id="boolean-key"),
            pytest.param({"on": {"pull_request": None}}, True, id="string-key"),
            pytest.param({True: ["pull_request"]}, True, id="boolean-key-list"),
            pytest.param({True: "pull_request"}, True, id="boolean-key-string"),
            pytest.param({True: {"push": {"branches": ["main"]}}}, False, id="push"),
            pytest.param({}, False, id="no-triggers"),
        ],
    )
    def test_pull_request_detection(
        self,
        document: dict[typ.Any, typ.Any],
        expected: bool,  # noqa: FBT001
    ) -> None:
        """A pull-request trigger is seen under either key and every spelling."""
        assert starts_on_pull_request(document) is expected

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param({True: {"push": {"branches": ["main"]}}}, True, id="main"),
            pytest.param({True: {"push": None}}, True, id="unfiltered"),
            pytest.param({True: {"push": {"branches": ["dev"]}}}, False, id="other"),
            pytest.param({True: {"workflow_dispatch": None}}, False, id="dispatch"),
        ],
    )
    def test_main_push_detection(
        self,
        document: dict[typ.Any, typ.Any],
        expected: bool,  # noqa: FBT001
    ) -> None:
        """A push to main is recognised under a filter and without one."""
        assert pushes_to_main(document) is expected

    def test_reachability_follows_a_called_workflow(self) -> None:
        """A caller a pull request starts drags its callee into the boundary."""
        documents: dict[str, WorkflowDocument] = {
            "caller.yml": typ.cast(
                "WorkflowDocument",
                {
                    True: {"pull_request": None},
                    "jobs": {"call": {"uses": f"{LOCAL_WORKFLOW_PREFIX}callee.yml"}},
                },
            ),
            "callee.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_call": None}, "jobs": {}}
            ),
            "unrelated.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_dispatch": None}, "jobs": {}}
            ),
        }
        assert pull_request_reachable(documents) == {"caller.yml", "callee.yml"}
