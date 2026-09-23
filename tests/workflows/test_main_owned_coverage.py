"""Contract-test the repository's main-owned CodeScene coverage flow (CV-005).

Under main-owned coverage no workflow a pull request can start may contact
CodeScene: pull-request lanes generate ratcheted coverage locally, and one
push-to-main publisher owns both the upload and the ratchet baseline every
pull request compares against. The uploader's own proof is split along the
same line: what contacts nothing stays on the pull-request lane, and the one
call that reads the project configuration is dispatch and trunk only.

The workflows are **enumerated** rather than named. A workflow added later is
covered the day it appears, and a caller job that delegates to a local
reusable workflow carries pull-request reachability into that file, so a
CodeScene call cannot hide one `uses:` away from the trigger that starts it.

Every assertion here quantifies over what it finds. The readings behind them
live in `workflow_boundary` and are driven on chosen inputs in
`test_workflow_boundary_reading`, because a reading that is wrong would
otherwise have to be wrong about a file this repository happens to contain
before anything fails.

Run via ``make test``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .publisher_binding import missing_bindings
from .pull_request_boundary import (
    credential_holders,
    host_namers,
    refused_self_references,
    secret_inheritors,
    service_callers,
)
from .test_coverage_timeout_tiers import (
    WORKFLOWS_DIRECTORY,
    WorkflowDocument,
    workflow_documents,
)
from .workflow_boundary import (
    CODESCENE_ACTION,
    CODESCENE_CREDENTIAL,
    CODESCENE_HOST,
    COVERAGE_LANGUAGE,
    COVERAGE_SCOPE,
    OFFLINE_MODE,
    RUN_UNIQUE_CONTEXTS,
    _publishers,
    _ratcheted_platforms,
    codescene_steps,
    coverage_steps,
    digest_offenders,
    digest_refreshers,
    pull_request_reachable,
    upload_steps,
)
from .workflow_expressions import TRUNK_REF_TERM, requires_every
from .workflow_triggers import declares_both_trigger_keys, pushes_to_main


@pytest.fixture(name="documents", scope="module")
def documents_fixture() -> dict[str, WorkflowDocument]:
    """Return this repository's parsed workflows, read when the tests run.

    The read belongs here rather than at import, so the readers stay pure over
    what they are given. The loading boundary reports a directory it cannot
    list, a workflow it cannot read, invalid YAML and a key declared twice as
    one ``ValueError`` naming the path; that fails every case in this module
    with the message rather than stopping collection of the whole directory.
    """
    try:
        return workflow_documents()
    except ValueError as error:
        pytest.fail(f"this repository's workflows cannot be read: {error}")


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

    def test_no_reachable_workflow_names_the_credential(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A lane that holds the token can contact the service by any means."""
        named = credential_holders(documents)
        assert named == [], (
            f"pull-request reachable workflows name {CODESCENE_CREDENTIAL}: {named}"
        )

    def test_no_reachable_workflow_names_the_codescene_host(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Forbid the service, not only the known ways of reaching it.

        The action, the client and the credential are the known doors. A step
        can reach the project API with a plain ``curl`` naming none of them,
        and every assertion beside this one would still pass.

        The comparison is case-insensitive because a DNS name is. The
        credential above is compared exactly, because an environment variable
        name is case-sensitive; the two are deliberately not folded together,
        and the asymmetry is the reason.
        """
        named = host_namers(documents)
        assert named == [], (
            f"pull-request reachable workflows reach {CODESCENE_HOST}: {named}"
        )

    def test_no_reachable_workflow_runs_a_service_subcommand(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The client's name is not the boundary; what it is asked to do is.

        ``install`` and ``version`` reach nothing, and the uploader's
        cold-runner proof needs them on every pull request that changes it.
        ``check`` and ``upload`` are the calls that read the project
        configuration.
        """
        named = service_callers(documents)
        assert named == {}, (
            f"pull-request reachable workflows call the service: {named}"
        )

    def test_no_reachable_job_inherits_every_secret(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """``secrets: inherit`` forwards the credential without naming it.

        A pull-request job forwards what it needs by name, where the
        credential reading above can see it.
        """
        inheritors = secret_inheritors(documents)
        assert inheritors == [], (
            "pull-request reachable jobs forward every secret, "
            f"{CODESCENE_CREDENTIAL} among them: {inheritors}"
        )

    def test_no_workflow_spells_its_trigger_key_both_ways(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """GitHub merges ``"on":`` and ``on:``; a reader picks one of them.

        A pull-request trigger under the key the reader skips would take the
        file out of the boundary, so such a file is refused outright.
        """
        both = sorted(
            name
            for name, document in documents.items()
            if declares_both_trigger_keys(document)
        )
        assert both == [], f"workflows declaring on: under both keys: {both}"

    def test_no_self_reference_carries_a_ref(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """``$/`` names the running commit; an ``@ref`` on it is refused."""
        refused = refused_self_references(documents)
        assert refused == [], f"$/ references carrying an @ref: {refused}"

    def test_no_reachable_workflow_uses_the_action_beyond_install(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A `uses:` is the other way a lane reaches CodeScene.

        The action's ``install`` mode downloads the pinned CLI and contacts
        CodeScene not at all, so it is the one mode a pull-request lane may
        ask for.
        """
        offenders = {
            f"{name}[{index}]": str((step.get("with") or {}).get("mode", ""))
            for name in sorted(pull_request_reachable(documents))
            for index, step in enumerate(codescene_steps(documents[name]))
            if str((step.get("with") or {}).get("mode", "")) != OFFLINE_MODE
        }
        assert offenders == {}, (
            f"pull-request reachable workflows invoke {CODESCENE_ACTION} in a "
            f"mode other than {OFFLINE_MODE}: {offenders}"
        )

    def test_the_cold_runner_proof_still_runs_on_pull_requests(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The boundary's other half: the offline proof must not drift away.

        Every rule above is satisfied by deleting the uploader's cold-runner
        proof outright. A pull request that changes the uploader has to keep
        getting it, so its presence on a pull-request path is contracted too.
        """
        reachable = pull_request_reachable(documents)
        installers = sorted(
            name
            for name in reachable
            if any(
                str((step.get("with") or {}).get("mode", "")) == OFFLINE_MODE
                for step in codescene_steps(documents[name])
            )
        )
        assert installers, (
            "no pull-request reachable workflow proves the uploader installs "
            f"the pinned CLI; reached {sorted(reachable)}"
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

    def test_no_pull_request_lane_can_also_write_the_baseline(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """One workflow advances the baseline, and it serves no pull request.

        A lane that serves pull requests and also runs on a push to main is a
        second writer: ``publish-baseline`` defaults to ``auto``, which saves
        on a trunk push whatever started the run. Two writers race, and the
        pull-request comparison then reads whichever won. Measured on lading
        (#276) where exactly this shape landed.
        """
        writers = sorted(
            name
            for name in pull_request_reachable(documents)
            if pushes_to_main(documents[name]) and coverage_steps(documents[name])
        )
        assert writers == [], (
            "these workflows serve pull requests and generate coverage on a "
            f"push to main, so they race the publisher's baseline: {writers}"
        )

    def test_every_lane_and_the_publisher_measure_the_same_population(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """The scope fixes what the percentages describe, so it is contracted.

        A baseline path alone is not enough. Dropping ``python-source`` from
        one lane leaves both naming the same file while measuring different
        populations, which is the incomparable comparison the scoped path was
        introduced to avoid.
        """
        (publisher,) = _publishers(documents)
        scopes = {
            f"{name}:{job}[{index}]": (
                str((step.get("with") or {}).get("language", "")),
                str((step.get("with") or {}).get("python-source", "")),
            )
            for name in sorted({*pull_request_reachable(documents), publisher})
            for index, (job, step) in enumerate(coverage_steps(documents[name]))
        }
        assert scopes, "no coverage step declares a language or a scope"
        expected = dict.fromkeys(scopes, (COVERAGE_LANGUAGE, COVERAGE_SCOPE))
        assert scopes == expected, (
            f"every coverage step must set language: {COVERAGE_LANGUAGE} and "
            f"python-source: {COVERAGE_SCOPE}; read {scopes}"
        )

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
        branch's coverage as the trunk's. Both halves are required terms of a
        conjunction, not substrings of the condition: appending
        ``|| github.event_name == 'workflow_dispatch'`` keeps both substrings
        and makes neither required.
        """
        (publisher,) = _publishers(documents)
        (upload,) = upload_steps(documents[publisher])
        guard = str(upload.get("if", ""))
        required = (TRUNK_REF_TERM, f"env.{CODESCENE_CREDENTIAL} != ''")
        assert requires_every(guard, required), (
            f"{publisher}'s upload guard does not require every one of "
            f"{required}; an unquoted || makes none of them required: {guard!r}"
        )

    def test_the_upload_step_is_given_the_credential(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """Assert the binding positively; the guard alone would hide its loss.

        With the binding deleted, ``env.CS_ACCESS_TOKEN != ''`` is simply
        false, the upload skips on every push, and nothing turns red.
        """
        (publisher,) = _publishers(documents)
        missing = missing_bindings(documents[publisher])
        assert missing == [], f"{publisher}'s upload step lacks: {missing}"

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

    def test_the_publisher_never_cancels_a_running_publication(
        self, documents: dict[str, WorkflowDocument]
    ) -> None:
        """A cancelled publisher abandons its upload and its baseline write.

        A newer push replaces a pending run instead, so the newest push's
        baseline still wins. Only an absent or literally false
        ``cancel-in-progress`` is accepted: an expression may evaluate true.
        """
        (publisher,) = _publishers(documents)
        concurrency = documents[publisher].get("concurrency")
        cancels = (
            concurrency.get("cancel-in-progress", False)
            if isinstance(concurrency, dict)
            else False
        )
        assert cancels is False or str(cancels).strip().lower() == "false", (
            f"{publisher} cancels an in-progress publication: "
            f"cancel-in-progress is {cancels!r}"
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
        refreshers = digest_refreshers(WORKFLOWS_DIRECTORY)
        assert refreshers == [], f"the digest refresher is back: {refreshers}"


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
