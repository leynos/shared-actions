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
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from .test_coverage_timeout_tiers import (
    WORKFLOWS_DIRECTORY,
    WorkflowDocument,
    WorkflowJob,
    workflow_documents,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The two ways a `uses:` names something in this repository. `./` is
#: workspace-relative and needs a checkout; `$/` resolves to the running
#: commit and must carry no `@ref` suffix. A reader that knows only `./`
#: lets a lane escape every boundary below by switching syntax.
SELF_PREFIXES: typ.Final[tuple[str, ...]] = ("./", "$/")
#: The local coverage action every lane invokes, without its prefix.
COVERAGE_ACTION: typ.Final[str] = ".github/actions/generate-coverage"
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
#: The path of a `uses:` naming a workflow in this repository, without its
#: self-repository prefix.
LOCAL_WORKFLOW_PATH: typ.Final[str] = ".github/workflows/"
#: Both file extensions GitHub reads a workflow from. A scan over one of them
#: is blind to a workflow spelled with the other.
WORKFLOW_PATTERNS: typ.Final[tuple[str, ...]] = ("*.yml", "*.yaml")
#: Contexts that make a concurrency group unique to one run. A group built
#: from any of them serialises nothing, because no two runs ever share it.
RUN_UNIQUE_CONTEXTS: typ.Final[tuple[str, ...]] = (
    "github.run_id",
    "github.run_number",
    "github.run_attempt",
    "github.sha",
    "github.job",
)


def _self_reference(uses: str, path: str) -> bool:
    """Return whether ``uses`` names ``path`` in this repository.

    A `$/` reference must not carry an `@ref` suffix, so one that does is
    not a valid self-reference and is not treated as one.
    """
    for prefix in SELF_PREFIXES:
        if not uses.startswith(f"{prefix}{path}"):
            continue
        return not (prefix == "$/" and "@" in uses)
    return False


def _self_reference_target(uses: str, path: str) -> str | None:
    """Return what a self-reference to ``path`` names, or ``None``.

    The `./` form may carry an `@ref`; the `$/` form may not, and one that
    does is rejected by :func:`_self_reference` rather than stripped.
    """
    if not _self_reference(uses, path):
        return None
    for prefix in SELF_PREFIXES:
        if uses.startswith(f"{prefix}{path}"):
            return uses.removeprefix(f"{prefix}{path}").split("@")[0]
    return None


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


def _matrix_labels(job: WorkflowJob) -> list[str]:
    """Return every value a job's matrix can substitute into ``runs-on``."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    return [
        str(value)
        for values in matrix.values()
        if isinstance(values, list)
        for value in values
    ]


def _runner_labels(job: WorkflowJob) -> list[str]:
    """Return the runner labels a job can run on.

    A ``runs-on`` naming a matrix value is resolved through the job's own
    matrix, so a Windows or macOS lane declared that way is not read as one
    indeterminate runner and quietly excused from the ratchet.
    """
    raw = job.get("runs-on")
    if isinstance(raw, str) and "${{" in raw:
        return _matrix_labels(job)
    if isinstance(raw, list):
        return [str(value) for value in raw]
    return [] if raw is None else [str(raw)]


def _platform_of(label: str) -> str:
    """Return the platform keyword a runner label names.

    The label is returned unchanged when it names none of them, so an
    unrecognised runner is carried into the comparison rather than dropped.
    """
    for keyword in ("ubuntu", "linux", "windows", "macos"):
        if keyword in label:
            return "ubuntu" if keyword == "linux" else keyword
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
    """
    found = {
        path.name: [
            marker
            for marker in (f"{CHECKSUM_INPUT}:", DIGEST_VARIABLE)
            if marker in path.read_text(encoding="utf-8")
        ]
        for pattern in WORKFLOW_PATTERNS
        for path in sorted(directory.glob(pattern))
    }
    return {name: markers for name, markers in found.items() if markers}


def digest_refreshers(directory: Path) -> list[str]:
    """Return any digest-refresher workflow in *directory*, under either name."""
    return sorted(
        path.name
        for pattern in WORKFLOW_PATTERNS
        for path in directory.glob(pattern)
        if path.stem == "get-codescene-sha"
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
        named = {name: found for name, found in offenders.items() if found}
        assert named == {}, f"pull-request reachable workflows name CodeScene: {named}"

    def test_no_reachable_workflow_invokes_the_codescene_action(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A `uses:` is the other way a lane reaches CodeScene."""
        offenders = {
            name: len(codescene_steps(documents[name]))
            for name in sorted(pull_request_reachable(documents))
            if codescene_steps(documents[name])
        }
        assert offenders == {}, (
            f"pull-request reachable workflows invoke {CODESCENE_ACTION}: {offenders}"
        )


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
        assert lanes, (
            "no pull-request lane generates coverage, so every rule below it "
            "passes over an empty set"
        )

    def test_every_lane_ratchets_and_withholds_the_artefact(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The publisher owns publication, so a lane keeps its report local."""
        readings = {
            f"{name}:{job}[{index}]": (
                str((step.get("with") or {}).get("with-ratchet", "")),
                str((step.get("with") or {}).get("publish-artefact", "")),
            )
            for name in sorted(pull_request_reachable(documents))
            for index, (job, step) in enumerate(coverage_steps(documents[name]))
        }
        expected = dict.fromkeys(readings, ("true", "false"))
        assert readings == expected, (
            "every pull-request coverage step sets with-ratchet true and "
            f"publish-artefact false; read {readings}"
        )


class TestTheLanesShareOneBaseline:
    """A ratchet compares against the file the publisher wrote, or nothing."""

    def test_every_lane_and_the_publisher_name_the_same_baseline_path(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A lane reading a path the publisher never writes ratchets against zero.

        The path is also how a scope change starts a fresh generation:
        narrowing ``python-source`` changes the measured population, so the
        percentages either side of the change are not comparable and the old
        baseline has to be left behind rather than compared with.
        """
        (publisher,) = _publishers(documents)
        paths = {
            f"{name}:{job}[{index}]": str(
                (step.get("with") or {}).get("baseline-python-file", "")
            )
            for name in sorted({*pull_request_reachable(documents), publisher})
            for index, (job, step) in enumerate(coverage_steps(documents[name]))
        }
        assert paths, "no coverage step names a baseline"
        assert len(set(paths.values())) == 1, (
            f"the coverage lanes disagree on the ratchet baseline path: {paths}"
        )
        assert "" not in set(paths.values()), (
            f"a coverage lane leaves the baseline path defaulted: {paths}"
        )


class TestMainOwnsPublication:
    """Exactly one push-to-main workflow uploads, under a guard and a lock."""

    def test_exactly_one_publisher(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """One file pushes to main, serves no pull request, and uploads."""
        publishers = _publishers(documents)
        assert publishers == ["coverage-main.yml"], (
            "exactly one workflow pushes to main, serves no pull request and "
            f"uploads; found {publishers}"
        )

    def test_no_other_workflow_uploads(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """An upload outside the publisher would race the baseline it writes."""
        uploaders = sorted(
            name for name, document in documents.items() if upload_steps(document)
        )
        assert uploaders == ["coverage-main.yml"], (
            f"workflows with a mode: upload step: {uploaders}"
        )

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
        assert "github.ref == 'refs/heads/main'" in guard, (
            f"{publisher}'s upload is not guarded on the trunk ref: {guard!r}"
        )
        assert f"env.{CODESCENE_CREDENTIAL} != ''" in guard, (
            f"{publisher}'s upload is not guarded on the credential: {guard!r}"
        )

    def test_the_publisher_is_serialised(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Two overlapping main pushes must not race to write the baseline.

        The group must also be stable across runs. A group built from
        ``github.run_id`` is non-empty and unique to its own run, so it
        serialises nothing while reading as present.
        """
        (publisher,) = _publishers(documents)
        concurrency = documents[publisher].get("concurrency")
        assert isinstance(concurrency, dict), (
            f"{publisher} declares no concurrency mapping: {concurrency!r}"
        )
        group = str(concurrency.get("group", "")).strip()
        assert group, f"{publisher}'s concurrency group is empty"
        unstable = [context for context in RUN_UNIQUE_CONTEXTS if context in group]
        assert not unstable, (
            f"{publisher}'s concurrency group {group!r} is unique per run "
            f"through {unstable}, so it serialises nothing"
        )

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
        assert lanes <= published, (
            f"{publisher} never ratchets these platforms, so their baselines "
            f"are never written: {sorted(lanes - published)}"
        )


class TestTheDeletedDigestPathStaysDeleted:
    """The installer-script digest and its refresher are gone for good."""

    def test_this_repository_carries_neither(self) -> None:
        """``installer-checksum`` is rejected when non-empty; the variable is dead."""
        named = digest_offenders(WORKFLOWS_DIRECTORY)
        assert named == {}, f"the digest path is back in: {named}"
        assert digest_refreshers(WORKFLOWS_DIRECTORY) == []

    @pytest.mark.parametrize("suffix", [".yml", ".yaml"])
    def test_an_offender_is_found_under_either_extension(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """Drive the scan on a directory built to contain one.

        Over this repository's compliant workflows the scan passes whether or
        not it reads both extensions, so the reading is exercised here. GitHub
        runs a workflow spelled either way.
        """
        (tmp_path / f"refresh{suffix}").write_text(
            f"on:\n  workflow_dispatch:\njobs:\n  a:\n    env:\n"
            f"      X: ${{{{ vars.{DIGEST_VARIABLE} }}}}\n",
            encoding="utf-8",
        )
        assert digest_offenders(tmp_path) == {f"refresh{suffix}": [DIGEST_VARIABLE]}

    @pytest.mark.parametrize("suffix", [".yml", ".yaml"])
    def test_a_refresher_is_found_under_either_extension(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """The refresher's only output was that variable (YAGNI, 2026-09-18)."""
        (tmp_path / f"get-codescene-sha{suffix}").write_text("{}", encoding="utf-8")
        assert digest_refreshers(tmp_path) == [f"get-codescene-sha{suffix}"]


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
        *,
        expected: bool,
    ) -> None:
        """A pull-request trigger is seen under either key and every spelling."""
        assert starts_on_pull_request(document) is expected, (
            f"{document!r} should read as pull-request started={expected}"
        )

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
        *,
        expected: bool,
    ) -> None:
        """A push to main is recognised under a filter and without one."""
        assert pushes_to_main(document) is expected, (
            f"{document!r} should read as main-push started={expected}"
        )

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/workflows/a.yml", "a.yml", id="relative"),
            pytest.param("$/.github/workflows/a.yml", "a.yml", id="self-repository"),
            pytest.param("./.github/workflows/a.yml@main", "a.yml", id="relative-ref"),
            pytest.param("$/.github/workflows/a.yml@main", None, id="self-ref-suffix"),
            pytest.param("other/repo/.github/workflows/a.yml@v1", None, id="foreign"),
            pytest.param("./.github/actions/a", None, id="an-action"),
            pytest.param("", None, id="a-step-job"),
        ],
    )
    def test_the_self_reference_reader_knows_both_spellings(
        self, uses: str, expected: str | None
    ) -> None:
        """Name the two prefixes literally, so narrowing the constant fails.

        Parametrising this over ``SELF_PREFIXES`` would make a reader that
        forgot ``$/`` pass with three fewer cases rather than fail, which is
        how a filtered list satisfies a rule by becoming empty.
        """
        assert _called_workflow({"uses": uses}) == expected, uses

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/actions/generate-coverage", True, id="relative"),
            pytest.param("$/.github/actions/generate-coverage", True, id="self-repo"),
            pytest.param(
                "$/.github/actions/generate-coverage@main", False, id="self-ref-suffix"
            ),
            pytest.param("./.github/actions/setup-rust", False, id="another-action"),
        ],
    )
    def test_the_coverage_action_is_recognised_in_both_spellings(
        self,
        uses: str,
        *,
        expected: bool,
    ) -> None:
        """A lane that switched syntax must not escape the coverage rules.

        A `$/` reference carrying an `@ref` is invalid to GitHub, so it is
        rejected rather than stripped and accepted.
        """
        document = {"jobs": {"a": {"steps": [{"uses": uses}]}}}
        assert bool(coverage_steps(document)) is expected, uses

    def test_reachability_follows_a_called_workflow(self) -> None:
        """A caller a pull request starts drags its callee into the boundary."""
        documents: dict[str, WorkflowDocument] = {
            "caller.yml": typ.cast(
                "WorkflowDocument",
                {
                    True: {"pull_request": None},
                    "jobs": {"call": {"uses": f"./{LOCAL_WORKFLOW_PATH}callee.yml"}},
                },
            ),
            "callee.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_call": None}, "jobs": {}}
            ),
            "unrelated.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_dispatch": None}, "jobs": {}}
            ),
        }
        reached = pull_request_reachable(documents)
        assert reached == {"caller.yml", "callee.yml"}, reached


#: One generated workflow: whether a pull request starts it, and which of the
#: graph's workflows its one job delegates to, by index.
WorkflowShape = tuple[bool, int | None]


def _graph(
    shapes: list[WorkflowShape], prefix: str = "./"
) -> dict[str, WorkflowDocument]:
    """Build a workflow graph from generated shapes.

    Each workflow gets at most one calling job, which is enough to express
    any reachability the real reader can meet: a caller with several jobs is
    the same relation with more edges. The self-repository prefix is a
    parameter, so the invariants hold for both spellings rather than for the
    one this repository happens to use today.
    """
    names = [f"w{index}.yml" for index in range(len(shapes))]
    documents: dict[str, WorkflowDocument] = {}
    for name, (starts, callee) in zip(names, shapes, strict=True):
        jobs: dict[str, typ.Any] = {}
        if callee is not None:
            jobs["call"] = {"uses": f"{prefix}{LOCAL_WORKFLOW_PATH}{names[callee]}"}
        documents[name] = typ.cast(
            "WorkflowDocument",
            {True: {"pull_request": None} if starts else {"push": None}, "jobs": jobs},
        )
    return documents


#: Graphs of up to six workflows, each with an optional edge to any of them.
#: Self-edges and cycles are generated deliberately: a traversal that marks
#: after visiting rather than before would not terminate on them, and this
#: repository's own workflows contain no cycle to find that with.
WORKFLOW_GRAPHS: typ.Final = st.integers(min_value=1, max_value=6).flatmap(
    lambda size: st.lists(
        st.tuples(
            st.booleans(),
            st.one_of(st.none(), st.integers(min_value=0, max_value=size - 1)),
        ),
        min_size=size,
        max_size=size,
    )
)


class TestReachabilityOverGeneratedGraphs:
    """The traversal's three invariants, on graphs chosen by Hypothesis.

    The repository's own workflows form a shallow forest with no cycle and
    one level of delegation, so the assertions above exercise the walk on a
    single shape. These state what the walk must be on any shape, which is
    what a contract quantifying over "every reachable workflow" relies on.
    """

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_every_pull_request_trigger_is_reached(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """A workflow a pull request starts is always inside the boundary."""
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        started = {
            name
            for name, document in documents.items()
            if starts_on_pull_request(document)
        }
        assert started <= reached, (
            f"pull-request started but unreached: {sorted(started - reached)}"
        )

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_the_boundary_is_closed_under_delegation(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Nothing a reached workflow calls is left outside.

        This is the half a CodeScene call moved one file away would exploit.
        """
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        for name in reached:
            escaped = _callees(documents, name) - reached
            assert not escaped, f"{name} calls unreached workflows: {sorted(escaped)}"

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_nothing_is_reached_without_a_reason(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Every member is pull-request started or called by another member.

        Without this half the walk could satisfy the other two by returning
        every workflow in the repository, which would make the boundary
        assertions fail on files no pull request can run.
        """
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        callers = {callee for name in reached for callee in _callees(documents, name)}
        for name in reached:
            assert starts_on_pull_request(documents[name]) or name in callers, (
                f"{name} is reached but nothing starts or calls it"
            )


def test_the_workflow_contracts_are_collected_by_the_default_run() -> None:
    """`testpaths` names the directory, so a module added later still runs.

    Naming files is how this module came to exist without running: the gate
    reported a clean suite while the contract below it was collected by
    nothing. This assertion is reached only when the module is collected, so
    it cannot prove its own collection; what it holds is the configuration
    that makes collection automatic for the next module too. The count is the
    real proof, and narrowing `testpaths` back to files moves it.
    """
    configuration = (Path(__file__).resolve().parents[2] / "pytest.ini").read_text(
        encoding="utf-8"
    )
    entries = {
        line.strip()
        for line in configuration.splitlines()
        if line.startswith((" ", "\t")) and line.strip()
    }
    assert "tests/workflows" in entries, (
        f"testpaths must name the workflow contracts directory; read {entries}"
    )
    narrowed = [entry for entry in entries if entry.startswith("tests/workflows/")]
    assert not narrowed, (
        f"testpaths names individual contract files, so the next one added "
        f"will be collected by nothing: {narrowed}"
    )
