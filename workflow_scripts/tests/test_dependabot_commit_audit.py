"""Commit-authorship gating, from the GraphQL response to the decision.

The unit tests beside this file exercise the rule and the message text.
These drive the production path instead: `main` calls the live handler,
which fetches, pages, audits, decides, and either arms auto-merge or
withdraws it. A rule that is right in isolation and never reached is the
failure this file exists to catch.

Every GraphQL call is mocked, so nothing here touches GitHub.
"""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts import dependabot_automerge, dependabot_commit_audit

# Test-only constant (not a real credential)
TEST_TOKEN = "test-token"  # noqa: S105

DEPENDABOT = "dependabot[bot]"

#: The head the audit reads, named to every merge mutation.
HEAD_OID = "0" * 40
MAINTAINER = "leynos"


class Mutation(typ.NamedTuple):
    """One mutation the script sent.

    The document is kept alongside the variables because a variable is
    only sent if the document declares it. An assertion on the variables
    alone passes with the field deleted from the input, which is the
    deletion that matters.

    Attributes
    ----------
    document : str
        The GraphQL document.
    variables : dict[str, object]
        The variables sent with it.
    """

    document: str
    variables: dict[str, object]

    def binds(self, name: str) -> bool:
        """Return whether the document passes ``name`` into its input.

        Parameters
        ----------
        name : str
            The input field, such as ``expectedHeadOid``.

        Returns
        -------
        bool
            True when the input names the field and takes it from the
            matching variable.
        """
        return f"{name}: ${name}" in self.document


class GraphQLCalls(typ.NamedTuple):
    """What the script asked GitHub to do.

    Attributes
    ----------
    enable : list[Mutation]
        Each ``enablePullRequestAutoMerge`` call.
    disable : list[Mutation]
        Each ``disablePullRequestAutoMerge`` call.
    merge : list[Mutation]
        Each ``mergePullRequest`` call.
    cursors : list[object]
        The commit cursor of each query, first page included, so a test
        can assert the connection was followed rather than read once.
    """

    enable: list[Mutation]
    disable: list[Mutation]
    merge: list[Mutation]
    cursors: list[object]


def _commit_node(oid: str, *logins: str, total: int | None = None) -> dict[str, object]:
    """Build one commit node as the GraphQL query returns it.

    Parameters
    ----------
    oid : str
        The commit SHA.
    *logins : str
        The logins credited on the commit.
    total : int or None
        ``totalCount`` for the author connection. Above the number of
        logins it marks the credit list as truncated; None means it
        matches.

    Returns
    -------
    dict
        The commit node.
    """
    return {
        "commit": {
            "oid": oid,
            "authors": {
                "totalCount": len(logins) if total is None else total,
                "nodes": [{"user": {"login": login}} for login in logins],
            },
        }
    }


def _pull_request_node(
    branch: Branch,
    served: typ.Sequence[typ.Sequence[dict[str, object]]],
    page_index: int,
) -> dict[str, object]:
    """Build the pull request node for one page of commits.

    The scalar fields come from the branch and the commits from the page
    set in use, which is not always the branch's own: the merge-state
    retry serves a second set, so that a push landing inside the retry
    window can be described.

    Parameters
    ----------
    branch : Branch
        The pull request GitHub should appear to hold.
    served : typ.Sequence[typ.Sequence[dict[str, object]]]
        The commit pages this fetch is serving.
    page_index : int
        Which of those pages this response carries.

    Returns
    -------
    dict
        The pull request node.
    """
    nodes = list(served[page_index])
    has_next = page_index + 1 < len(served)
    return {
        "id": "PR_node",
        "number": 7,
        "headRefOid": HEAD_OID,
        "isDraft": branch.is_draft,
        "mergeStateStatus": branch.merge_state,
        "mergeable": "MERGEABLE",
        "author": {"login": DEPENDABOT},
        "labels": {"nodes": [{"name": "dependencies"}]},
        "autoMergeRequest": branch.auto_merge_request,
        "commits": {
            "totalCount": sum(len(page) for page in served),
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": f"cursor-{page_index + 1}" if has_next else None,
            },
            "nodes": nodes,
        },
    }


def _mutation_response(
    query: str, variables: dict[str, object], calls: GraphQLCalls
) -> dict[str, object] | None:
    """Record and answer a mutation, or return None for a query.

    Parameters
    ----------
    query : str
        The GraphQL document.
    variables : dict[str, object]
        Its variables, recorded so a test can assert on them.
    calls : GraphQLCalls
        The record to append to.

    Returns
    -------
    dict[str, object] or None
        The mutation's response, or None when this is not a mutation.
    """
    mutations = (
        ("enablePullRequestAutoMerge", calls.enable, {"pullRequest": {"number": 7}}),
        ("disablePullRequestAutoMerge", calls.disable, {"pullRequest": {"number": 7}}),
        (
            "mergePullRequest",
            calls.merge,
            {"pullRequest": {"number": 7, "merged": True}},
        ),
    )
    for name, record, payload in mutations:
        if name in query:
            record.append(Mutation(document=query, variables=variables))
            return {name: payload}
    return None


class _ServedBranch:
    """Serves commit pages, switching sets after the first whole fetch.

    The merge-state retry refetches the pull request from the first page,
    which is the only way a test can put a push inside the retry window:
    the second fetch must see a different branch from the first.
    """

    def __init__(
        self,
        pages: typ.Sequence[typ.Sequence[dict[str, object]]],
        later_pages: typ.Sequence[typ.Sequence[dict[str, object]]] | None,
    ) -> None:
        self._pages = pages
        self._later_pages = later_pages
        self._fetches = 0

    def page_for(
        self, cursor: object
    ) -> tuple[typ.Sequence[typ.Sequence[dict[str, object]]], int]:
        """Return the page set to serve and the index within it.

        Parameters
        ----------
        cursor : object
            The requested commit cursor, None for the first page.

        Returns
        -------
        tuple
            The page set and the index of the requested page.
        """
        if cursor is None:
            self._fetches += 1
            return self._served, 0
        return self._served, int(str(cursor).rsplit("-", 1)[1])

    @property
    def _served(self) -> typ.Sequence[typ.Sequence[dict[str, object]]]:
        """Return the page set the current fetch should see.

        Returns
        -------
        typ.Sequence[typ.Sequence[dict[str, object]]]
            The commit pages.
        """
        if self._later_pages is not None and self._fetches > 1:
            return self._later_pages
        return self._pages


class Branch(typ.NamedTuple):
    """The pull request a test wants GitHub to appear to hold.

    One value rather than a parameter list, because these four describe
    one situation, and a call naming three of them by keyword read as
    though the fourth had been forgotten.

    Attributes
    ----------
    pages : typ.Sequence[typ.Sequence[dict[str, object]]]
        Commit pages served to the first fetch of the pull request.
    auto_merge_request : dict or None
        The ``autoMergeRequest`` field. Not None means already armed.
    merge_state : str
        The ``mergeStateStatus`` to report.
    later_pages : typ.Sequence[typ.Sequence[dict[str, object]]] or None
        Commit pages served from the second fetch onward, for the case of
        a push landing inside the merge-state retry window.
    is_draft : bool
        Whether the pull request is a draft, which is a skip reason other
        than a foreign commit.
    """

    pages: typ.Sequence[typ.Sequence[dict[str, object]]]
    auto_merge_request: dict[str, object] | None = None
    merge_state: str = "BLOCKED"
    later_pages: typ.Sequence[typ.Sequence[dict[str, object]]] | None = None
    is_draft: bool = False


#: An auto-merge request already armed when the run starts.
ARMED: typ.Final[dict[str, object]] = {
    "enabledAt": "2026-09-06T00:00:00Z",
    "mergeMethod": "SQUASH",
}


def _install_graphql(monkeypatch: pytest.MonkeyPatch, branch: Branch) -> GraphQLCalls:
    """Replace the GraphQL client with a scripted one, and record calls.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture.
    branch : Branch
        The pull request GitHub should appear to hold.

    Returns
    -------
    GraphQLCalls
        The record the test asserts against.
    """
    calls = GraphQLCalls(enable=[], disable=[], merge=[], cursors=[])
    state = _ServedBranch(branch.pages, branch.later_pages)

    def handler(
        _token: str, query: str, variables: dict[str, object]
    ) -> dict[str, object]:
        mutation = _mutation_response(query, variables, calls)
        if mutation is not None:
            return mutation
        cursor = variables.get("commitCursor")
        calls.cursors.append(cursor)
        served, index = state.page_for(cursor)
        return {
            "repository": {"pullRequest": _pull_request_node(branch, served, index)}
        }

    monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)
    return calls


def _run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke the CLI entry point against the mocked API.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture, used to clear the event path.
    """
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=7,
        ),
    )


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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [
                        _commit_node("aaaa1111", DEPENDABOT),
                        _commit_node("cccc3333", MAINTAINER),
                    ]
                ]
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [
                        _commit_node("aaaa1111", DEPENDABOT),
                        _commit_node("bbbb2222", "dependabot"),
                    ]
                ]
            ),
        )

        _run(monkeypatch)

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
        _install_graphql(
            monkeypatch,
            Branch(pages=[[_commit_node("dddd4444", DEPENDABOT, MAINTAINER)]]),
        )

        _run(monkeypatch)

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
            node = _pull_request_node(Branch(pages=[[]]), [[]], 0)
            del node["commits"]
            return {"repository": {"pullRequest": node}}

        monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [
                        _commit_node(f"aaaa{index:04d}", DEPENDABOT)
                        for index in range(3)
                    ],
                    [_commit_node("cccc3333", MAINTAINER)],
                ]
            ),
        )

        _run(monkeypatch)

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
        _install_graphql(
            monkeypatch,
            Branch(pages=[[_commit_node("eeee5555", DEPENDABOT, total=101)]]),
        )

        _run(monkeypatch)

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
        _install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [_commit_node("aaaa1111", DEPENDABOT)],
                    [_commit_node("bbbb2222", DEPENDABOT)],
                    [_commit_node("cccc3333", DEPENDABOT)],
                ]
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(pages=[[_commit_node("ffff6666")]]),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[
                    [_commit_node("aaaa1111", DEPENDABOT)],
                    [_commit_node("bbbb2222", DEPENDABOT)],
                    [_commit_node("cccc3333", MAINTAINER)],
                ]
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(pages=[[_commit_node("aaaa1111", DEPENDABOT)]]),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[[_commit_node("aaaa1111", DEPENDABOT)]],
                merge_state="CLEAN",
            ),
        )

        _run(monkeypatch)

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
            node = _pull_request_node(
                Branch(pages=[[_commit_node("aaaa1111", DEPENDABOT)]]),
                [[_commit_node("aaaa1111", DEPENDABOT)]],
                0,
            )
            del node["headRefOid"]
            return {"repository": {"pullRequest": node}}

        monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)

        with pytest.raises(SystemExit):
            _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[[_commit_node("cccc3333", MAINTAINER)]],
                auto_merge_request=ARMED,
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[[_commit_node("aaaa1111", DEPENDABOT)]],
                auto_merge_request=ARMED,
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[[_commit_node("aaaa1111", DEPENDABOT)]],
                auto_merge_request=ARMED,
                is_draft=True,
            ),
        )

        _run(monkeypatch)

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
        calls = _install_graphql(
            monkeypatch,
            Branch(
                pages=[[_commit_node("aaaa1111", DEPENDABOT)]],
                merge_state="UNKNOWN",
                later_pages=[[_commit_node("cccc3333", MAINTAINER)]],
            ),
        )

        _run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:cccc3333" in out, out
        assert not calls.enable, "the refreshed snapshot must gate the decision"


_LOGINS = st.sampled_from([DEPENDABOT, "dependabot", MAINTAINER, "renovate[bot]", ""])


@st.composite
def _commit_records(
    draw: st.DrawFn,
) -> tuple[dependabot_commit_audit.CommitRecord, ...]:
    """Generate a branch's commits with arbitrary authorship.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis's draw function.

    Returns
    -------
    tuple[CommitRecord, ...]
        A bounded branch.
    """
    count = draw(st.integers(min_value=0, max_value=8))
    records: list[dependabot_commit_audit.CommitRecord] = []
    for index in range(count):
        logins = draw(st.lists(_LOGINS, min_size=0, max_size=4))
        complete = draw(st.booleans())
        authors = tuple(
            login or dependabot_commit_audit.UNKNOWN_AUTHOR for login in logins
        )
        records.append(
            dependabot_commit_audit.CommitRecord(
                oid=f"{index:040x}",
                authors=authors,
                authors_complete=complete,
            )
        )
    return tuple(records)


def _is_dependabot_only(record: dependabot_commit_audit.CommitRecord) -> bool:
    """Return whether one commit passes the eligibility rule.

    Stated here independently of the implementation, so the property
    tests compare two readings of the rule rather than one reading with
    itself. A commit crediting nobody fails: the rule certifies on
    evidence, and no credited author is no evidence.

    Parameters
    ----------
    record : dependabot_commit_audit.CommitRecord
        The commit to judge.

    Returns
    -------
    bool
        True when the commit is Dependabot's and wholly read.
    """
    if not record.authors:
        return False
    if not record.authors_complete:
        return False
    return all(
        author in dependabot_commit_audit.DEPENDABOT_LOGINS for author in record.authors
    )


class TestTheRuleHoldsOverArbitraryBranches:
    """Invariants of the rule, over branches nobody wrote down."""

    @given(records=_commit_records())
    def test_a_commit_is_reported_exactly_when_it_fails_the_rule(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The rule is total: every commit is judged, and judged once.

        Stating it as a property catches the family of defects a handful
        of examples cannot: a loop that stops at the first offender, a
        branch that reports a commit twice, or one that skips the commit
        after a match.
        """
        found = dependabot_commit_audit.foreign_commits(records)
        expected = [record.oid for record in records if not _is_dependabot_only(record)]
        assert [commit.oid for commit in found] == expected, (
            f"every failing commit must be reported once, in branch order; "
            f"got {[commit.oid for commit in found]} for {records}"
        )

    @given(records=_commit_records())
    def test_a_reported_commit_always_names_something(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The notice is the only record a maintainer sees.

        A blank author would leave the log saying a commit was rejected
        by nobody, which is unactionable.
        """
        for commit in dependabot_commit_audit.foreign_commits(records):
            assert commit.author, f"{commit.oid} was reported with no author"

    @given(
        records=_commit_records().filter(
            lambda records: all(_is_dependabot_only(record) for record in records)
        )
    )
    def test_a_wholly_dependabot_branch_is_never_reported(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The gate must not stop the bumps it exists to let through.

        Both login variants count, in any mixture, on any number of
        commits. A commit crediting nobody is excluded: it is not a
        Dependabot commit, it is a commit with no evidence either way.
        """
        assert dependabot_commit_audit.foreign_commits(records) == (), (
            f"a branch written only by Dependabot must pass: {records}"
        )
