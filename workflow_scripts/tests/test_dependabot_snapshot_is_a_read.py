"""Obtaining a snapshot must not also act on one.

The auto-merge script decides whether a dependency bump may land
unreviewed. The helper that reads the pull request used to reach its
`None` return by reporting a decision and, on one path, withdrawing an
armed auto-merge request: a name that promised a value and delivered
consequences, so a caller could not obtain a snapshot in order to look
at it.

Splitting the read out is only worth anything if the split is asserted.
These drive an ineligible branch, the case where the old shape acted,
and require that the read alone changes nothing at GitHub.
"""

from __future__ import annotations

import typing as typ

from workflow_scripts import dependabot_automerge
from workflow_scripts.dependabot_decision import MergeMethod
from workflow_scripts.tests.dependabot_graphql_double import (
    ARMED,
    Branch,
    build_graphql,
    commit_node,
)

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    import pytest

    from workflow_scripts.tests.dependabot_graphql_double import GraphQLCalls

TEST_TOKEN = "test-token"  # noqa: S105 - a stand-in, not a credential


def _run(handler: object) -> dependabot_automerge.LiveRun:
    """Return a live run over ``handler`` with the ordinary configuration."""
    return dependabot_automerge.LiveRun(
        token=TEST_TOKEN,
        query=typ.cast("typ.Any", handler),
        config=dependabot_automerge.AutomergeConfig(
            merge_method=MergeMethod.SQUASH,
            required_label="dependencies",
            dry_run=False,
        ),
    )


def _context() -> dependabot_automerge.RuntimeContext:
    """Return a runtime context naming a pull request."""
    return dependabot_automerge.RuntimeContext(
        repo_full_name="acme/example",
        event=None,
        pull_request_number=7,
    )


#: A branch carrying a commit Dependabot did not write, with auto-merge
#: already armed on it. This is the situation in which the old helper
#: withdrew the request while calling itself a read, so it is the one
#: that distinguishes the two shapes.
FOREIGN_AND_ARMED = Branch(
    pages=[
        [
            commit_node("aaaaaaaa1111", "dependabot[bot]"),
            commit_node("cccccccc3333", "someone-else"),
        ]
    ],
    auto_merge_request=ARMED,
)


def _mutations(calls: GraphQLCalls) -> dict[str, int]:
    """Return how many of each mutation the script asked for."""
    return {
        "enable": len(calls.enable),
        "disable": len(calls.disable),
        "merge": len(calls.merge),
    }


class TestReadingASnapshotChangesNothing:
    """The read is a read, and is asserted to be one."""

    def test_reading_an_ineligible_branch_issues_no_mutation(
        self, monkeypatch: object
    ) -> None:
        """A branch that will be refused is still only read.

        The refusal is the caller's to make. If it were made here, a
        caller could not ask what the pull request says without thereby
        answering it.
        """
        del monkeypatch
        handler, calls = build_graphql(FOREIGN_AND_ARMED)

        dependabot_automerge._read_snapshot(_context(), run=_run(handler))

        assert _mutations(calls) == {"enable": 0, "disable": 0, "merge": 0}, (
            f"the read issued a mutation: {_mutations(calls)}"
        )

    def test_reading_returns_the_snapshot_rather_than_a_verdict(self) -> None:
        """The read hands back what GitHub said, refusal or not.

        The shape this replaces returned None for an ineligible branch,
        so the only way to learn what the pull request said was to have
        already acted on it.
        """
        handler, _calls = build_graphql(FOREIGN_AND_ARMED)

        pr = dependabot_automerge._read_snapshot(_context(), run=_run(handler))

        assert pr is not None, "an ineligible branch still has a snapshot"
        assert pr.auto_merge_enabled, (
            "the snapshot must carry what GitHub reported, including the "
            "armed request the caller will decide to withdraw"
        )

    def test_the_whole_run_still_withdraws_the_armed_request(self) -> None:
        """Moving the decision out must not lose it.

        The counterpart to the two above: they say the read does not
        withdraw, and this says something still does. Without it, the
        split could be satisfied by removing the withdrawal entirely,
        which is the defect the audit exists to prevent.
        """
        handler, calls = build_graphql(FOREIGN_AND_ARMED)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        assert len(calls.disable) == 1, (
            f"the armed request on a foreign branch was not withdrawn: "
            f"{_mutations(calls)}"
        )
        assert not calls.enable, _mutations(calls)
        assert not calls.merge, _mutations(calls)


#: A pull request with nothing left to do, whose merge state is the one
#: value the refresh retries on. Dependabot wrote every commit and
#: auto-merge is already armed, so the run's answer is known before the
#: refresh could change anything.
ARMED_AND_UNSETTLED = Branch(
    pages=[[commit_node("aaaaaaaa1111", "dependabot[bot]")]],
    auto_merge_request=ARMED,
    merge_state="UNKNOWN",
)


class TestTheFirstJudgementIsWorthMaking:
    """Judging before the refresh is not merely tidy ordering.

    The refresh is a retry loop that sleeps while mergeability is
    unknown. A pull request with auto-merge already armed has no answer
    the refresh could change, so judging first is what stops the run
    waiting out the whole schedule to report what it knew at the start.

    Without this, deleting the first judgement changes nothing any test
    could see, and the ordering would be free to drift back.
    """

    def test_an_armed_request_is_reported_without_a_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to do means one read, not a retry schedule."""
        # Bounded so that a regression is slow rather than interminable:
        # if the refresh does run, this decides how long the failure takes.
        monkeypatch.setenv("AUTOMERGE_MERGE_STATE_MAX_ATTEMPTS", "2")
        monkeypatch.setenv("AUTOMERGE_MERGE_STATE_BASE_SLEEP_SECONDS", "0")
        monkeypatch.setenv("AUTOMERGE_MERGE_STATE_MAX_SLEEP_SECONDS", "0")
        handler, calls = build_graphql(ARMED_AND_UNSETTLED)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        assert len(calls.cursors) == 1, (
            "an already-armed pull request was refetched; the run refreshed a "
            f"merge state whose answer it could not use: {calls.cursors}"
        )
        assert _mutations(calls) == {"enable": 0, "disable": 0, "merge": 0}, (
            f"nothing should have been mutated: {_mutations(calls)}"
        )
