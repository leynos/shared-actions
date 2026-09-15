"""Snapshots of everything the auto-merge run writes to a job.

The outputs and notices are this action's interface: a caller's workflow
branches on `automerge_status`, and a maintainer reads the notice. Both
are easy to change by accident, because every test that asserts on them
asserts on the one line it cares about and passes while a neighbouring
line is renamed or dropped.

These pin the whole block instead. The values are chosen here rather than
found, so nothing in a snapshot identifies a real repository, pull
request or author.
"""

from __future__ import annotations

import typing as typ

import pytest

from workflow_scripts.dependabot_commit_audit import ForeignCommit
from workflow_scripts.dependabot_decision import (
    AutomergeConfig,
    Decision,
    DecisionStatus,
    PullRequestContext,
)
from workflow_scripts.dependabot_merge_state import MergeableState, MergeStateStatus
from workflow_scripts.dependabot_report import emit_decision, emit_withdrawal_notice
from workflow_scripts.output import fail

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

#: Neutral stand-ins, so a snapshot names nothing real.
OWNER = "acme"
REPO = "example"
NUMBER = 7
AUTHOR = "dependabot[bot]"
OID = "0" * 40

CONFIG = AutomergeConfig(
    merge_method="SQUASH",
    required_label="dependencies",
    dry_run=False,
)


def _context(
    *,
    foreign_commits: tuple[ForeignCommit, ...] = (),
    commits_readable: bool = True,
    commit_pages_read: int = 1,
    commits_audited: int = 3,
) -> PullRequestContext:
    """Return a pull request snapshot with the fields a test varies.

    The four the snapshots vary are named parameters rather than
    ``**overrides``. A ``dict[str, object]`` splatted into the
    constructor takes a misspelt field name and a wrongly typed value
    from every caller, and needs a suppression on the constructor that
    would hide a real mismatch as readily as the false one.

    Parameters
    ----------
    foreign_commits : tuple of ForeignCommit
        Commits on the branch that Dependabot did not write.
    commits_readable : bool
        Whether the commit list could be read at all.
    commit_pages_read : int
        How many pages of the commit connection were fetched.
    commits_audited : int
        How many commits were judged.

    Returns
    -------
    PullRequestContext
        The snapshot.
    """
    return PullRequestContext(
        node_id="PR_node",
        number=NUMBER,
        owner=OWNER,
        repo=REPO,
        author=AUTHOR,
        is_draft=False,
        labels=("dependencies",),
        head_oid=OID,
        merge_state_status=MergeStateStatus.BLOCKED,
        mergeable_state=MergeableState.MERGEABLE,
        foreign_commits=foreign_commits,
        commits_readable=commits_readable,
        commit_pages_read=commit_pages_read,
        commits_audited=commits_audited,
    )


class TestTheDecisionBlock:
    """The `key=value` lines a caller's workflow reads back."""

    def test_an_armed_branch_reports_every_field(
        self, capsys: pytest.CaptureFixture[str], snapshot: SnapshotAssertion
    ) -> None:
        """A clean branch emits the whole block and no notice.

        Every field is emitted unconditionally, so the block describes
        the decision whether or not anything merged. A snapshot is what
        notices a field being dropped; an assertion on one line does not.
        """
        emit_decision(
            _context(),
            Decision(status=DecisionStatus.ENABLED, reason="enabled"),
            config=CONFIG,
        )

        assert capsys.readouterr().out == snapshot

    def test_a_dry_run_never_claims_a_merge(
        self, capsys: pytest.CaptureFixture[str], snapshot: SnapshotAssertion
    ) -> None:
        """A dry run reports `dry-run` where it would report `ready`.

        The substitution happens in the reporting rather than in the
        rule, so it is only visible here.
        """
        emit_decision(
            _context(),
            Decision(status=DecisionStatus.READY, reason="eligible"),
            config=AutomergeConfig(
                merge_method="SQUASH", required_label="dependencies", dry_run=True
            ),
        )

        assert capsys.readouterr().out == snapshot


class TestTheNotices:
    """What a maintainer reads when a branch does not merge."""

    def test_a_foreign_commit_is_named(
        self, capsys: pytest.CaptureFixture[str], snapshot: SnapshotAssertion
    ) -> None:
        """A branch carrying foreign commits names them in a notice.

        The check has done its job here, so this is a notice rather than
        a warning, and the distinction is part of what is pinned.
        """
        emit_decision(
            _context(
                foreign_commits=(ForeignCommit(oid="a" * 40, author="maintainer"),)
            ),
            Decision(
                status=DecisionStatus.SKIPPED, reason=f"foreign-commit:{'a' * 40}"
            ),
            config=CONFIG,
        )

        assert capsys.readouterr().out == snapshot

    def test_an_unreadable_audit_warns_instead(
        self, capsys: pytest.CaptureFixture[str], snapshot: SnapshotAssertion
    ) -> None:
        """A branch whose commits could not be read warns.

        There the check did not run at all and eligibility rests on the
        pull request's author alone, which is a degradation rather than
        an outcome, so it is a warning and says what is not covered.
        """
        emit_decision(
            _context(commits_readable=False, commit_pages_read=0, commits_audited=0),
            Decision(status=DecisionStatus.SKIPPED, reason="commits-unreadable"),
            config=CONFIG,
        )

        assert capsys.readouterr().out == snapshot

    def test_a_withdrawal_says_what_it_undid(
        self, capsys: pytest.CaptureFixture[str], snapshot: SnapshotAssertion
    ) -> None:
        """Withdrawing an armed request explains why it was withdrawn.

        This one changed the pull request rather than merely declining
        to act on it, so the wording has to say so.
        """
        emit_withdrawal_notice(_context())

        assert capsys.readouterr().out == snapshot


class TestTheFailurePath:
    """The one status emitted from outside the decision module."""

    def test_a_failure_reports_the_error_status(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`fail` emits exactly the text `DecisionStatus.ERROR` carries.

        `output.fail` sits below the decision module and cannot import
        the enum without closing a cycle, so it writes the literal. That
        makes the two free to drift, and a caller's workflow branching on
        `automerge_status` would be the first to notice. This holds them
        equal: the assertion is on what `fail` actually writes to stderr,
        so deleting or misspelling the literal fails it.
        """
        with pytest.raises(SystemExit):
            fail("anything")

        assert (
            f"automerge_status={DecisionStatus.ERROR.value}\n"
            in capsys.readouterr().err
        ), "output.fail must emit the status DecisionStatus.ERROR names"
