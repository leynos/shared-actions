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

from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts import dependabot_automerge, dependabot_commit_audit

if typ.TYPE_CHECKING:
    import pytest

# Test-only constant (not a real credential)
TEST_TOKEN = "test-token"  # noqa: S105

DEPENDABOT = "dependabot[bot]"
MAINTAINER = "leynos"


class GraphQLCalls(typ.NamedTuple):
    """What the script asked GitHub to do.

    Attributes
    ----------
    enable : list[dict[str, object]]
        Variables of each ``enablePullRequestAutoMerge`` call.
    disable : list[dict[str, object]]
        Variables of each ``disablePullRequestAutoMerge`` call.
    merge : list[dict[str, object]]
        Variables of each ``mergePullRequest`` call.
    cursors : list[object]
        The commit cursor of each query, first page included, so a test
        can assert the connection was followed rather than read once.
    """

    enable: list[dict[str, object]]
    disable: list[dict[str, object]]
    merge: list[dict[str, object]]
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
    pages: typ.Sequence[typ.Sequence[dict[str, object]]],
    page_index: int,
    *,
    auto_merge_request: dict[str, object] | None,
    merge_state: str,
) -> dict[str, object]:
    """Build the pull request node for one page of commits.

    Parameters
    ----------
    pages : typ.Sequence[typ.Sequence[dict[str, object]]]
        The commit pages, in branch order.
    page_index : int
        Which page this response carries.
    auto_merge_request : dict or None
        The ``autoMergeRequest`` field; not None means already armed.
    merge_state : str
        The ``mergeStateStatus`` to report.

    Returns
    -------
    dict
        The pull request node.
    """
    nodes = list(pages[page_index])
    has_next = page_index + 1 < len(pages)
    return {
        "id": "PR_node",
        "number": 7,
        "isDraft": False,
        "mergeStateStatus": merge_state,
        "mergeable": "MERGEABLE",
        "author": {"login": DEPENDABOT},
        "labels": {"nodes": [{"name": "dependencies"}]},
        "autoMergeRequest": auto_merge_request,
        "commits": {
            "totalCount": sum(len(page) for page in pages),
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
            record.append(variables)
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


def _install_graphql(
    monkeypatch: pytest.MonkeyPatch,
    pages: typ.Sequence[typ.Sequence[dict[str, object]]],
    *,
    auto_merge_request: dict[str, object] | None = None,
    merge_state: str = "BLOCKED",
    later_pages: typ.Sequence[typ.Sequence[dict[str, object]]] | None = None,
) -> GraphQLCalls:
    """Replace the GraphQL client with a scripted one, and record calls.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture.
    pages : typ.Sequence[typ.Sequence[dict[str, object]]]
        Commit pages served to the first fetch of the pull request.
    auto_merge_request : dict or None
        The ``autoMergeRequest`` field to report.
    merge_state : str
        The ``mergeStateStatus`` to report.
    later_pages : typ.Sequence[typ.Sequence[dict[str, object]]] or None
        Commit pages served from the second fetch onward, for the case of
        a push landing inside the merge-state retry window.

    Returns
    -------
    GraphQLCalls
        The record the test asserts against.
    """
    calls = GraphQLCalls(enable=[], disable=[], merge=[], cursors=[])
    state = _ServedBranch(pages, later_pages)

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
            "repository": {
                "pullRequest": _pull_request_node(
                    served,
                    index,
                    auto_merge_request=auto_merge_request,
                    merge_state=merge_state,
                )
            }
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
            [
                [
                    _commit_node("aaaa1111", DEPENDABOT),
                    _commit_node("cccc3333", MAINTAINER),
                ]
            ],
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
            [
                [
                    _commit_node("aaaa1111", DEPENDABOT),
                    _commit_node("bbbb2222", "dependabot"),
                ]
            ],
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
            [[_commit_node("dddd4444", DEPENDABOT, MAINTAINER)]],
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
            node = _pull_request_node(
                [[]], 0, auto_merge_request=None, merge_state="BLOCKED"
            )
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
            [
                [_commit_node(f"aaaa{index:04d}", DEPENDABOT) for index in range(3)],
                [_commit_node("cccc3333", MAINTAINER)],
            ],
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
            [[_commit_node("eeee5555", DEPENDABOT, total=101)]],
        )

        _run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_reason=foreign-commit:eeee5555" in out, out
        assert "an unread co-author" in out, out

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
            [
                [_commit_node("aaaa1111", DEPENDABOT)],
                [_commit_node("bbbb2222", DEPENDABOT)],
                [_commit_node("cccc3333", MAINTAINER)],
            ],
        )

        _run(monkeypatch)

        out = capsys.readouterr().out
        assert "automerge_commit_audit=unreadable" in out, out
        assert len(calls.cursors) == 2, calls.cursors


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
            [[_commit_node("cccc3333", MAINTAINER)]],
            auto_merge_request={
                "enabledAt": "2026-09-06T00:00:00Z",
                "mergeMethod": "SQUASH",
            },
        )

        _run(monkeypatch)

        out = capsys.readouterr().out
        assert len(calls.disable) == 1, "the armed request must be withdrawn"
        assert calls.disable[0]["pullRequestId"] == "PR_node", calls.disable
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
            [[_commit_node("aaaa1111", DEPENDABOT)]],
            auto_merge_request={
                "enabledAt": "2026-09-06T00:00:00Z",
                "mergeMethod": "SQUASH",
            },
        )

        _run(monkeypatch)

        out = capsys.readouterr().out
        assert not calls.disable, "an eligible branch keeps its armed request"
        assert "automerge_status=enabled" in out, out

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
            [[_commit_node("aaaa1111", DEPENDABOT)]],
            merge_state="UNKNOWN",
            later_pages=[[_commit_node("cccc3333", MAINTAINER)]],
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
        expected = [
            record.oid
            for record in records
            if not record.authors_complete
            or any(
                author not in dependabot_commit_audit.DEPENDABOT_LOGINS
                for author in record.authors
            )
        ]
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
            lambda records: all(
                record.authors_complete
                and all(
                    author in dependabot_commit_audit.DEPENDABOT_LOGINS
                    for author in record.authors
                )
                for record in records
            )
        )
    )
    def test_a_wholly_dependabot_branch_is_never_reported(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The gate must not stop the bumps it exists to let through.

        Both login variants count, in any mixture, on any number of
        commits.
        """
        assert dependabot_commit_audit.foreign_commits(records) == (), (
            f"a branch written only by Dependabot must pass: {records}"
        )
