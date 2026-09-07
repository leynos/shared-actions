"""Commit-authorship gating, from the GraphQL response to the decision.

The unit tests beside this file exercise the rule and the message text,
and :mod:`test_dependabot_audit_rule` states its invariants. These drive
the production path instead: `main` calls the live handler, which
fetches, pages, audits, decides, and either arms auto-merge, merges, or
withdraws a request. A rule that is right in isolation and never reached
is the failure this file exists to catch.

Every GraphQL call is scripted by :mod:`dependabot_graphql_double`, so
nothing here touches GitHub.
"""

from __future__ import annotations

import pytest
from dependabot_graphql_double import (
    ARMED,
    DEPENDABOT,
    HEAD_OID,
    MAINTAINER,
    Branch,
    commit_node,
    install_graphql,
    pull_request_node,
    run,
)

from workflow_scripts import dependabot_automerge


class TestTheProductionPathReachesTheRule:
    """From a GraphQL response to an emitted decision."""

    def test_a_foreign_commit_stops_the_merge_and_is_named(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The case the change exists for, driven end to end.

        A rule that rejects the commit but is never consulted by the live
        handler would leave this branch merging unattended, so the
        assertion is on the emitted decision rather than on the audit.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [
                        commit_node("aaaa1111", DEPENDABOT),
                        commit_node("cccc3333", MAINTAINER),
                    ]
                ]
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_status=skipped" in out, out
        assert "automerge_reason=foreign-commit:cccc3333" in out, out
        assert "automerge_commit_audit=foreign" in out, out
        assert "cccc3333 by leynos" in out, "the notice must name the commit"
        assert not calls.enable, "auto-merge must not be armed on a foreign branch"
        assert not calls.merge, "the branch must not be merged directly either"

    def test_an_all_dependabot_branch_still_arms_auto_merge(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The ordinary bump must keep working.

        A gate that rejected everything would pass the test above and
        break every consumer, so the passing case is asserted too.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [
                        commit_node("aaaa1111", DEPENDABOT),
                        commit_node("bbbb2222", "dependabot"),
                    ]
                ]
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_status=enabled" in out, out
        assert "automerge_commit_audit=clean" in out, out
        assert len(calls.enable) == 1, calls.enable

    def test_a_co_authored_commit_counts_as_foreign(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Co-authorship is authorship for this purpose.

        A commit Dependabot pushed but a person co-wrote carries that
        person's change, so the branch is not Dependabot's alone.
        """
        install_graphql(
            monkeypatch,
            Branch(pages=[[commit_node("dddd4444", DEPENDABOT, MAINTAINER)]]),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:dddd4444" in out, out

    def test_an_unreadable_commit_list_fails_open_with_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A query fault must not halt every consumer at once.

        The loss of the check is louder than its absence: the run still
        proceeds, and says in the log that the check did not run.
        """

        def handler(
            _token: str, query: str, variables: dict[str, object]
        ) -> dict[str, object]:
            if "enablePullRequestAutoMerge" in query:
                return {"enablePullRequestAutoMerge": {"pullRequest": {"number": 7}}}
            node = pull_request_node(Branch(pages=[[]]), [[]], 0)
            del node["commits"]
            return {"repository": {"pullRequest": node}}

        monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_status=enabled" in out, out
        assert "automerge_commit_audit=unreadable" in out, out
        assert "could not read the commits" in out, "the loss must be visible"


class TestTheWholeBranchIsAudited:
    """Reading one page of a connection is not reading the branch."""

    def test_a_foreign_commit_beyond_the_first_page_is_found(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A connection read to its limit looks complete and is not.

        The nodes come back, the audit runs, and a commit past the page
        size is never seen. Paging is what closes that gap, so the
        foreign commit is placed on the second page deliberately.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [commit_node(f"aaaa{index:04d}", DEPENDABOT) for index in range(3)],
                    [commit_node("cccc3333", MAINTAINER)],
                ]
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:cccc3333" in out, out
        assert calls.cursors == [None, "cursor-1"], calls.cursors
        assert not calls.enable, "the second page must gate the decision too"

    def test_a_truncated_author_list_is_treated_as_foreign(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A credit list read to its limit certifies nothing.

        Unlike a commit list that never arrived, this commit is visible
        and merely unread to the end, so the safe reading is that an
        unseen co-author might be a person.
        """
        install_graphql(
            monkeypatch,
            Branch(pages=[[commit_node("eeee5555", DEPENDABOT, total=101)]]),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:eeee5555" in out, out
        assert "an unread co-author" in out, out

    def test_the_run_reports_how_much_of_the_branch_it_read(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A count is the difference between one page and the branch.

        `automerge_commit_audit=clean` says the same thing whether one
        page or four were read, so a paging fault would look exactly like
        a short branch. The counts are what make that visible, and they
        are counts rather than identifiers so the lines stay countable.
        """
        install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [commit_node("aaaa1111", DEPENDABOT)],
                    [commit_node("bbbb2222", DEPENDABOT)],
                    [commit_node("cccc3333", DEPENDABOT)],
                ]
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_commit_pages_read=3" in out, out
        assert "automerge_commits_audited=3" in out, out

    def test_a_commit_crediting_nobody_is_treated_as_foreign(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A visible commit with no author evidence certifies nothing.

        This is the shape a rule looking only for outsiders waves
        through: an empty credit list has no login outside Dependabot's,
        so nothing objects, and the branch merges unreviewed on the
        strength of a commit nobody is recorded as writing.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(pages=[[commit_node("ffff6666")]]),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:ffff6666" in out, out
        assert "an unnamed author" in out, out
        assert not calls.enable, "a commit crediting nobody must not merge"

    def test_a_branch_beyond_the_page_ceiling_is_reported_unreadable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The paging loop is bounded rather than trusting the cursor.

        Following pages forever on a server that never stops offering
        them would hang the job with no decision at all, which is worse
        than an audit that says plainly it did not finish.
        """
        monkeypatch.setattr(dependabot_automerge, "MAX_COMMIT_PAGES", 2)
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [commit_node("aaaa1111", DEPENDABOT)],
                    [commit_node("bbbb2222", DEPENDABOT)],
                    [commit_node("cccc3333", MAINTAINER)],
                ]
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_commit_audit=unreadable" in out, out
        assert len(calls.cursors) == 2, calls.cursors


class TestEveryMergeNamesTheAuditedHead:
    """The audit binds the merge, or it is only advice."""

    def test_arming_auto_merge_names_the_head_the_audit_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A push between the audit and the mutation is the whole risk.

        GitHub makes no head-match check without ``expectedHeadOid``, so
        the request would be armed on a head nobody looked at, and the
        audit would have been advice rather than a gate.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(pages=[[commit_node("aaaa1111", DEPENDABOT)]]),
        )

        run(monkeypatch)

        assert len(calls.enable) == 1, calls.enable
        armed = calls.enable[0]
        assert armed.binds("expectedHeadOid"), (
            "the arming mutation must pass expectedHeadOid into its input; a "
            "variable the document does not declare is not sent at all"
        )
        assert armed.variables["expectedHeadOid"] == HEAD_OID, (
            f"arming must name the audited head, got "
            f"{armed.variables.get('expectedHeadOid')!r}"
        )

    def test_merging_directly_names_the_head_the_audit_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The direct merge carries the same risk and the same guard.

        An already-mergeable pull request is merged outright rather than
        armed, so it needs the binding just as much.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[[commit_node("aaaa1111", DEPENDABOT)]],
                merge_state="CLEAN",
            ),
        )

        run(monkeypatch)

        assert len(calls.merge) == 1, calls.merge
        merged = calls.merge[0]
        assert merged.binds("expectedHeadOid"), (
            "the merge mutation must pass expectedHeadOid into its input; a "
            "variable the document does not declare is not sent at all"
        )
        assert merged.variables["expectedHeadOid"] == HEAD_OID, (
            f"the direct merge must name the audited head, got "
            f"{merged.variables.get('expectedHeadOid')!r}"
        )

    def test_a_response_with_no_head_refuses_to_merge(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without a head there is nothing to bind the audit to.

        Sending the mutation anyway would act on whatever the head is at
        that moment, which is the case the binding exists to refuse.
        """

        def handler(
            _token: str, query: str, variables: dict[str, object]
        ) -> dict[str, object]:
            if "enablePullRequestAutoMerge" in query:
                message = "arming must not be attempted without a head"
                raise AssertionError(message)
            node = pull_request_node(
                Branch(pages=[[commit_node("aaaa1111", DEPENDABOT)]]),
                [[commit_node("aaaa1111", DEPENDABOT)]],
                0,
            )
            del node["headRefOid"]
            return {"repository": {"pullRequest": node}}

        monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)

        with pytest.raises(SystemExit):
            run(monkeypatch)

        err = capsys.readouterr().err
        assert "unaudited head" in err, err


class TestAnArmedRequestIsWithdrawn:
    """Declining to arm auto-merge is not enough once it is armed."""

    def test_a_foreign_push_cancels_the_existing_request(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GitHub keeps an auto-merge request alive across a push.

        A request armed while the branch was Dependabot's would merge the
        commit that made it foreign as soon as the required checks
        passed, which is exactly the outcome this check exists to
        prevent. Skipping this run would not stop it.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[[commit_node("cccc3333", MAINTAINER)]],
                auto_merge_request=ARMED,
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert len(calls.disable) == 1, "the armed request must be withdrawn"
        assert calls.disable[0].variables["pullRequestId"] == "PR_node", calls.disable
        assert "automerge_status=cancelled" in out, out
        assert "automerge_reason=foreign-commit:cccc3333" in out, out
        assert "cancelled the auto-merge request" in out, out

    def test_a_clean_branch_with_a_request_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Withdrawal is for foreign branches, not for every skip.

        Cancelling an armed request on an ordinary bump would undo the
        arming this workflow exists to do.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[[commit_node("aaaa1111", DEPENDABOT)]],
                auto_merge_request=ARMED,
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert not calls.disable, "an eligible branch keeps its armed request"
        assert "automerge_status=enabled" in out, out

    def test_a_branch_skipped_for_another_reason_keeps_its_request(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Withdrawal answers a foreign commit, not every skip.

        A draft pull request is skipped too, and cancelling its armed
        request would mean a maintainer marking a bump ready found the
        arming silently undone. The distinction only holds if something
        exercises a skip that is not a foreign commit; a test whose
        branch is eligible never reaches the withdrawal at all.
        """
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[[commit_node("aaaa1111", DEPENDABOT)]],
                auto_merge_request=ARMED,
                is_draft=True,
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=draft-pr" in out, out
        assert not calls.disable, (
            "a draft branch written only by Dependabot must keep its armed "
            "request; the withdrawal is for foreign commits alone"
        )

    def test_a_push_inside_the_retry_window_is_caught(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The refreshed snapshot decides, not the one that prompted it.

        An unknown merge state makes the handler sleep and refetch. That
        refetch also refetches who wrote the commits, so a push landing
        in the window is visible in it and nowhere else. Arming on the
        strength of the earlier snapshot would merge it unreviewed.
        """
        monkeypatch.setenv("AUTOMERGE_MERGE_STATE_BASE_SLEEP_SECONDS", "0")
        calls = install_graphql(
            monkeypatch,
            Branch(
                pages=[[commit_node("aaaa1111", DEPENDABOT)]],
                merge_state="UNKNOWN",
                later_pages=[[commit_node("cccc3333", MAINTAINER)]],
            ),
        )

        run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:cccc3333" in out, out
        assert not calls.enable, "the refreshed snapshot must gate the decision"


def test_the_read_path_uses_the_query_it_is_given() -> None:
    """The GraphQL call is an argument, not a module attribute.

    Patching a module attribute makes every caller in the process use
    the substitute, so a test cannot describe one read path without
    describing all of them. Passing the call proves the parameter is
    honoured rather than shadowed by the default, which is the whole
    point of taking it.
    """
    pages = [
        [commit_node("b" * 40, DEPENDABOT)],
        [commit_node("c" * 40, DEPENDABOT)],
    ]
    branch = Branch(pages=pages)
    asked: list[str] = []

    def _query(
        _token: str, query: str, _variables: dict[str, object]
    ) -> dict[str, object]:
        page_index = len(asked)
        asked.append(query)
        return {
            "repository": {"pullRequest": pull_request_node(branch, pages, page_index)}
        }

    pull_request = dependabot_automerge._fetch_pull_request(
        "token", "leynos", "shared-actions", 1, query=_query
    )

    assert len(asked) == len(pages), (
        "the first fetch and every further page must go through the supplied "
        f"query; it was called {len(asked)} time(s) for {len(pages)} pages"
    )
    assert not pull_request.foreign_commits, (
        "the audit read this branch through the supplied query"
    )
